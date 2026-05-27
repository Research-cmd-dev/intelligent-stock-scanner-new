"""Maximum-history daily OHLCV + dividends/splits downloader (yfinance).

Writes one Parquet per ticker into MAX_HISTORICAL_ROOT
(default: data/historical_max locally, /data/historical_max on Modal).

This store is deliberately separate from the OHLCV-only `historical/`
used by the backtester because the column set (including Dividends,
Stock Splits, Adj Close) and "period=max" semantics differ.

Spec followed exactly:
- yfinance.download(period="max", auto_adjust=False, actions=True)
- Columns: Open, High, Low, Close, Adj Close, Volume, Dividends, Stock Splits
- DatetimeIndex tz-stripped, named "date"
- MOG.A kept for yf, filename becomes MOG-A.parquet
- Resumable (skip non-empty existing parquet)
- 3 retries + exp backoff per ticker
- Empty results (new IPOs, delisted, bad symbols) -> status "empty"
- _manifest.parquet with sectors list per ticker
- Summary counts

Usage (local):
    python -m src.data.max_history --workers 8 --force

Usage (Modal volume):
    modal run -m src.modal_app.app::download_max
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# yfinance is intentionally imported inside the worker so that the module
# remains importable in environments that have not installed it yet.
# The Modal image always has it via requirements.txt.

from src.config import get_settings
from src.config.sectors import SECTOR_TICKERS
from src.utils import get_logger

log = get_logger(__name__)


# SECTOR_TICKERS is the canonical 16-sector / 560-unique universe imported from
# src.config.sectors. Re-exported here so existing imports
# (`from src.data.max_history import SECTOR_TICKERS`) keep working.
__all_sector_tickers__ = SECTOR_TICKERS


def build_ticker_sectors() -> dict[str, list[str]]:
    """Return {ticker: sorted list of sectors it belongs to}."""
    mapping: dict[str, list[str]] = defaultdict(list)
    for sector, tickers in SECTOR_TICKERS.items():
        for t in tickers:
            mapping[t].append(sector)
    # sort for determinism
    return {t: sorted(sects) for t, sects in mapping.items()}


def all_unique_tickers() -> list[str]:
    """Sorted unique tickers across all sectors (560)."""
    return sorted(build_ticker_sectors().keys())


# --------------------------------------------------------------------------- #
# Storage root (mirrors the pattern in historical.py)
# --------------------------------------------------------------------------- #

def max_history_root() -> Path:
    """Resolve the directory for the max-history parquet files + manifest."""
    explicit = os.getenv("MAX_HISTORICAL_ROOT")
    if explicit:
        root = Path(explicit)
    else:
        root = get_settings().repo_root / "data" / "historical_max"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ticker_to_path(ticker: str, root: Path | None = None) -> Path:
    """Filesystem path: dots become dashes (MOG.A -> MOG-A.parquet)."""
    safe = ticker.replace(".", "-").replace("/", "-")
    return (root or max_history_root()) / f"{safe}.parquet"


# --------------------------------------------------------------------------- #
# Core download worker (with retry, resumable, empty handling)
# --------------------------------------------------------------------------- #

TARGET_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits"]


@dataclass
class TickerResult:
    ticker: str
    status: str          # downloaded | skipped | empty | failed
    n_rows: int = 0
    start_date: str | None = None
    end_date: str | None = None
    error: str | None = None
    sectors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # ensure sectors is always a list (for parquet)
        if d["sectors"] is None:
            d["sectors"] = []
        return d


def _strip_tz(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.tz is not None:
        index = index.tz_localize(None)
    # normalize to midnight just in case intraday sneaks in
    return pd.DatetimeIndex(pd.to_datetime(index).normalize(), name="date")


def _download_one_yf(ticker: str) -> pd.DataFrame:
    """Single yfinance call exactly as specified."""
    import yfinance as yf  # local import so the rest of the module works without it

    # period="max" + actions=True + auto_adjust=False gives us the 8 columns we want
    df = yf.download(
        ticker,
        period="max",
        auto_adjust=False,
        actions=True,
        progress=False,
    )
    if df is None:
        return pd.DataFrame()
    # yf.download on a single string returns a DataFrame (not MultiIndex columns for recent versions)
    # Some older yf versions may still return a 1-level column index with the fields.
    if isinstance(df.columns, pd.MultiIndex):
        # Defensive: flatten if somehow multi-level
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    # Guarantee exactly the columns the user asked for (fill missing with zeros)
    for col in TARGET_COLUMNS:
        if col not in df.columns:
            df[col] = 0 if col != "Volume" else 0
    df = df[TARGET_COLUMNS].copy()

    # Clean index
    if len(df) > 0:
        df.index = _strip_tz(pd.DatetimeIndex(df.index))
        df = df.sort_index()
    else:
        df.index = pd.DatetimeIndex([], name="date")

    # Drop any completely empty rows (rare)
    df = df.dropna(how="all")
    return df


def download_ticker(
    ticker: str,
    *,
    force: bool = False,
    max_retries: int = 3,
) -> TickerResult:
    """Download (or skip) one ticker. Returns structured result for the manifest."""
    root = max_history_root()
    path = _ticker_to_path(ticker, root)

    # Resumable check
    if not force and path.exists():
        try:
            existing = pd.read_parquet(path)
            if len(existing) > 0:
                idx = existing.index
                return TickerResult(
                    ticker=ticker,
                    status="skipped",
                    n_rows=len(existing),
                    start_date=str(idx.min().date()) if len(idx) else None,
                    end_date=str(idx.max().date()) if len(idx) else None,
                )
        except Exception as exc:  # corrupted file -> re-download
            log.warning("existing parquet for %s unreadable (%s) -> re-download", ticker, exc)

    sectors = build_ticker_sectors().get(ticker, [s for s, tlist in SECTOR_TICKERS.items() if ticker in tlist])

    last_err: str | None = None
    for attempt in range(max_retries):
        try:
            df = _download_one_yf(ticker)

            if df.empty or len(df) == 0:
                # Write an empty frame anyway so we don't hammer it again
                df.to_parquet(path)
                return TickerResult(ticker=ticker, status="empty", n_rows=0, sectors=sectors)

            # Success path
            df.to_parquet(path)
            return TickerResult(
                ticker=ticker,
                status="downloaded",
                n_rows=len(df),
                start_date=str(df.index.min().date()),
                end_date=str(df.index.max().date()),
                sectors=sectors,
            )

        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            if attempt < max_retries - 1:
                sleep_s = (2 ** attempt) * 1.2 + 0.5  # ~1.7s, 3.4s, 6.8s
                log.info("retry %s (%s/%s) after %.1fs: %s", ticker, attempt + 1, max_retries, sleep_s, last_err)
                time.sleep(sleep_s)

    # All retries exhausted
    return TickerResult(ticker=ticker, status="failed", n_rows=0, error=last_err, sectors=sectors)


# --------------------------------------------------------------------------- #
# Batch orchestrator + manifest
# --------------------------------------------------------------------------- #

@dataclass
class MaxDownloadReport:
    started_at: str
    finished_at: str
    elapsed_s: float
    root: str
    results: list[TickerResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def downloaded(self) -> int:
        return sum(1 for r in self.results if r.status == "downloaded")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def empty(self) -> int:
        return sum(1 for r in self.results if r.status == "empty")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "downloaded": self.downloaded,
            "skipped": self.skipped,
            "empty": self.empty,
            "failed": self.failed,
            "root": self.root,
            "elapsed_s": round(self.elapsed_s, 1),
        }

    def to_manifest_df(self) -> pd.DataFrame:
        rows = [r.to_dict() for r in self.results]
        df = pd.DataFrame(rows)
        # nice column order
        cols = ["ticker", "status", "n_rows", "start_date", "end_date", "sectors", "error"]
        df = df[[c for c in cols if c in df.columns]]
        return df.sort_values("ticker").reset_index(drop=True)


def download_max_history(
    *,
    tickers: list[str] | None = None,
    force: bool = False,
    max_workers: int = 8,
    progress_every: int = 25,
) -> MaxDownloadReport:
    """Download the max-history dataset for the given tickers (or the full union)."""
    started = time.time()
    started_iso = datetime.now(tz=timezone.utc).isoformat()

    ticker_sectors = build_ticker_sectors()
    if tickers is None:
        tickers = sorted(ticker_sectors.keys())
    else:
        tickers = sorted({t.strip().upper() for t in tickers})

    root = max_history_root()
    log.info("max-history download starting: %d tickers -> %s (workers=%d, force=%s)",
             len(tickers), root, max_workers, force)

    results: list[TickerResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_to_t = {pool.submit(download_ticker, t, force=force): t for t in tickers}
        for i, fut in enumerate(as_completed(fut_to_t), 1):
            res = fut.result()
            results.append(res)
            if i % progress_every == 0 or i == len(tickers):
                log.info("  progress: %d/%d  last=%s (%s, rows=%s)",
                         i, len(tickers), res.ticker, res.status, res.n_rows)

    results.sort(key=lambda r: r.ticker)

    finished = time.time()
    report = MaxDownloadReport(
        started_at=started_iso,
        finished_at=datetime.now(tz=timezone.utc).isoformat(),
        elapsed_s=finished - started,
        root=str(root),
        results=results,
    )

    # Write manifest (always, even on partial runs)
    manifest_path = root / "_manifest.parquet"
    manifest_df = report.to_manifest_df()
    manifest_df.to_parquet(manifest_path, index=False)
    log.info("manifest written: %s (%d rows)", manifest_path, len(manifest_df))

    log.info(
        "max-history complete: total=%d downloaded=%d skipped=%d empty=%d failed=%d elapsed=%.1fs",
        report.total, report.downloaded, report.skipped, report.empty, report.failed, report.elapsed_s
    )
    return report


# --------------------------------------------------------------------------- #
# CLI (python -m src.data.max_history)
# --------------------------------------------------------------------------- #

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.data.max_history",
        description="Download maximum-history yfinance data + dividends/splits into the Modal volume (or local).",
    )
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if a non-empty parquet already exists.")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel download workers (default 8).")
    parser.add_argument("--tickers", help="Comma-separated subset (for testing).")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only process the first N tickers (testing).")
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")

    args = parser.parse_args(argv)

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = None

    if args.limit and tickers is None:
        tickers = all_unique_tickers()[: args.limit]

    report = download_max_history(tickers=tickers, force=args.force, max_workers=args.workers)

    summary = report.to_summary_dict()
    if args.json:
        import json
        print(json.dumps(summary, indent=2))
    else:
        print("\n=== Max-History yfinance Download Summary ===")
        print(f"  root:        {summary['root']}")
        print(f"  total:       {summary['total']}")
        print(f"  downloaded:  {summary['downloaded']}")
        print(f"  skipped:     {summary['skipped']}")
        print(f"  empty:       {summary['empty']}")
        print(f"  failed:      {summary['failed']}")
        print(f"  elapsed:     {summary['elapsed_s']}s")
        if report.failed:
            print("\nFailures:")
            for r in report.results:
                if r.status == "failed":
                    print(f"  {r.ticker}: {r.error}")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
