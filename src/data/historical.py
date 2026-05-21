"""Durable historical OHLCV store.

This module is the long-term home for daily price history used by the
backtest, feature, and (eventually) intelligence layers. It is
intentionally separate from ``src.data.fetcher`` and its sibling
``data/cache/`` directory:

* ``data/cache/`` — short-lookback, refreshed every trading day, used
  by the live dashboard. Owned by ``fetcher.py``.
* ``data/historical/`` — full history per symbol, append-only,
  incrementally updated. Owned by this module.

The root directory is resolved from ``STOCK_DATA_ROOT`` so that the
same code path runs locally in Codespaces (defaults to
``data/historical/``) and on Modal (volume-mounted at
``/data/historical``). Nothing else in the codebase knows or cares
which environment it is running in.

Public surface:

* :func:`load_history` / :func:`load_panel` — read a stored frame.
* :func:`update_symbol` / :func:`download_universe` — write / refresh.
* :func:`warm_cache_from_historical` — bridge to ``data/cache/`` so the
  unchanged backtest can run on top of a Modal-populated volume.
* ``python -m src.data.historical {download|info|list}`` — CLI.

All public functions degrade gracefully: a symbol that fails to
download is logged and skipped, never raised, so a 5000-name sweep
finishes even when a handful of tickers misbehave.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from src.utils import get_current_utc_date
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.config import get_settings
from src.config.sectors import all_tickers, tickers_for_sectors
from src.utils import get_logger

from . import polygon_client, yfinance_client

log = get_logger(__name__)


# ---------------------------------------------------------------------- #
# Configuration                                                          #
# ---------------------------------------------------------------------- #

# Default first-pull window for a brand-new symbol.
# 10 years gives multiple full market cycles (2008 GFC, 2020 COVID, 2022 bear,
# etc.), excellent warmup for long-term features (SMA200, volatility regimes,
# multi-year momentum), and is still cheap to store/serve as daily bars.
# Users who want "all available history" can pass --days 20000 (or similar).
DEFAULT_FULL_HISTORY_DAYS = 365 * 10

# Extra days requested on top of the last-stored gap when updating, so
# we tolerate weekends, holidays, and any provider delay.
INCREMENTAL_PAD_DAYS = 7

# Default parallelism for batch downloads. Conservative — Polygon free
# tier rate-limits aggressively; users with paid plans can raise this.
DEFAULT_MAX_WORKERS = 8

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


def historical_root() -> Path:
    """Resolve the on-disk root for historical parquet files.

    Order of precedence:

    1. ``STOCK_DATA_ROOT`` env var (Modal sets this to point at the
       mounted ``stock_data`` volume).
    2. ``data/historical/`` under the repo root.
    """
    explicit = os.getenv("STOCK_DATA_ROOT")
    if explicit:
        root = Path(explicit)
    else:
        root = get_settings().repo_root / "data" / "historical"
    root.mkdir(parents=True, exist_ok=True)
    return root


def backtest_root() -> Path:
    """Resolve the on-disk root for backtest artifacts.

    Defaults to ``logs/`` locally (same as the existing report layer)
    but Modal jobs point this at ``/data/backtests`` on the volume by
    setting ``STOCK_BACKTEST_ROOT``.
    """
    explicit = os.getenv("STOCK_BACKTEST_ROOT")
    if explicit:
        root = Path(explicit)
    else:
        root = get_settings().log_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def symbol_path(symbol: str, *, root: Path | None = None) -> Path:
    """Canonical path for ``symbol``'s parquet file."""
    return (root or historical_root()) / f"{symbol.upper()}.parquet"


# ---------------------------------------------------------------------- #
# Read side                                                              #
# ---------------------------------------------------------------------- #


def load_history(
    symbol: str,
    *,
    start: str | date | pd.Timestamp | None = None,
    end: str | date | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Read stored OHLCV for ``symbol``, optionally sliced to a window.

    Returns an empty frame (with the canonical columns) if the symbol
    has never been downloaded. The index is a tz-naive
    ``DatetimeIndex`` named ``date``.
    """
    path = symbol_path(symbol)
    if not path.exists():
        return _empty_frame()

    df = pd.read_parquet(path)
    df = _normalize_frame(df)

    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def load_panel(
    symbols: Iterable[str],
    *,
    start: str | date | pd.Timestamp | None = None,
    end: str | date | pd.Timestamp | None = None,
    field_name: str | None = None,
) -> dict[str, pd.DataFrame] | pd.DataFrame:
    """Load many symbols at once.

    When ``field_name`` is ``None`` (the default) returns a
    ``{symbol: DataFrame}`` mapping suitable for the existing backtest
    engine. When a single field name is given (``"close"``, ``"volume"``,
    …) returns a wide DataFrame with one column per symbol — handy for
    cross-sectional feature work.
    """
    frames: dict[str, pd.DataFrame] = {}
    for sym in sorted({s.upper() for s in symbols}):
        df = load_history(sym, start=start, end=end)
        if df.empty:
            continue
        frames[sym] = df

    if field_name is None:
        return frames

    if field_name not in OHLCV_COLUMNS:
        raise ValueError(
            f"field_name must be one of {OHLCV_COLUMNS}, got {field_name!r}"
        )
    series_map = {sym: df[field_name] for sym, df in frames.items()}
    if not series_map:
        return pd.DataFrame()
    return pd.DataFrame(series_map).sort_index()


def last_stored_date(symbol: str) -> pd.Timestamp | None:
    """Latest bar date in storage for ``symbol``, or ``None`` if absent."""
    path = symbol_path(symbol)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=[])  # index-only
    except Exception as exc:  # pragma: no cover - corrupt file
        log.warning("could not read %s: %s", path, exc)
        return None
    if df.empty:
        return None
    return pd.Timestamp(df.index.max())


# ---------------------------------------------------------------------- #
# Write side                                                             #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of refreshing one symbol."""

    symbol: str
    status: str  # "created" | "updated" | "unchanged" | "skipped" | "error"
    new_rows: int = 0
    total_rows: int = 0
    last_date: str | None = None
    source: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class DownloadReport:
    """Aggregate outcome of a batch download."""

    started_at: str
    finished_at: str
    elapsed_s: float
    results: list[UpdateResult]
    root: str

    @property
    def created(self) -> int:
        return sum(1 for r in self.results if r.status == "created")

    @property
    def updated(self) -> int:
        return sum(1 for r in self.results if r.status == "updated")

    @property
    def unchanged(self) -> int:
        return sum(1 for r in self.results if r.status == "unchanged")

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def total_new_rows(self) -> int:
        return sum(r.new_rows for r in self.results)

    def to_dict(self) -> dict:
        return {
            **{k: v for k, v in asdict(self).items() if k != "results"},
            "results": [asdict(r) for r in self.results],
            "summary": {
                "created": self.created,
                "updated": self.updated,
                "unchanged": self.unchanged,
                "errors": self.errors,
                "total_new_rows": self.total_new_rows,
            },
        }


def update_symbol(symbol: str, *, force: bool = False, lookback_days: int | None = None) -> UpdateResult:
    """Pull any missing bars for ``symbol`` and merge into the store.

    * If no parquet exists yet, pulls ``lookback_days`` (or DEFAULT_FULL_HISTORY_DAYS)
      of history and creates it.
    * If a parquet exists, pulls only the gap between ``last_stored +
      1`` and today (plus a pad for non-trading days).
    * ``force=True`` re-pulls the requested window (or default) and overwrites.
    * ``lookback_days`` can be passed to override the default history depth for new/forced symbols.

    Never raises — failures return an ``UpdateResult`` with
    ``status="error"`` so a batch loop can keep going.
    """
    sym = symbol.upper()
    path = symbol_path(sym)
    existing = None if force else (load_history(sym) if path.exists() else None)
    last = None if existing is None or existing.empty else existing.index.max()

    today = pd.Timestamp(get_current_utc_date())
    if last is not None and last >= today:
        # Already up to date through today; nothing to do.
        return UpdateResult(
            symbol=sym,
            status="unchanged",
            total_rows=int(len(existing)) if existing is not None else 0,
            last_date=str(last.date()),
        )

    if last is None:
        lookback = lookback_days or DEFAULT_FULL_HISTORY_DAYS
    else:
        lookback = max(1, (today - last).days) + INCREMENTAL_PAD_DAYS

    try:
        fresh, source = _fetch_with_fallback(sym, lookback)
    except Exception as exc:  # noqa: BLE001
        log.warning("download failed for %s: %s", sym, exc)
        return UpdateResult(symbol=sym, status="error", error=str(exc))

    if existing is None or existing.empty:
        merged = fresh
        status = "created"
    else:
        before = len(existing)
        merged = _merge(existing, fresh)
        if len(merged) == before:
            return UpdateResult(
                symbol=sym,
                status="unchanged",
                total_rows=before,
                last_date=str(existing.index.max().date()),
                source=source,
            )
        status = "updated"

    new_rows = (
        len(merged) if existing is None or existing.empty
        else len(merged) - len(existing)
    )
    _atomic_write_parquet(merged, path)

    return UpdateResult(
        symbol=sym,
        status=status,
        new_rows=int(new_rows),
        total_rows=int(len(merged)),
        last_date=str(merged.index.max().date()),
        source=source,
    )


def download_universe(
    symbols: Iterable[str],
    *,
    force: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
    progress: bool = False,
    lookback_days: int | None = None,
) -> DownloadReport:
    """Refresh the historical store for many symbols in parallel.

    Concurrency is thread-based because the underlying clients spend
    almost all their time on HTTP I/O. ``max_workers`` defaults to a
    conservative 8; raise it when you have a paid Polygon plan.

    If ``lookback_days`` is provided, new symbols (or forced refreshes)
    will pull that many days of history instead of the default 10-year window.
    """
    started = time.time()
    started_iso = datetime.now(tz=timezone.utc).isoformat()
    syms = sorted({s.upper() for s in symbols})

    results: list[UpdateResult] = []
    log.info("downloading %d symbols (workers=%d, force=%s)",
             len(syms), max_workers, force)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(update_symbol, s, force=force, lookback_days=lookback_days): s
            for s in syms
        }
        for i, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            results.append(result)
            if progress and i % 25 == 0:
                log.info("  progress: %d/%d (last=%s)", i, len(syms), result.symbol)

    results.sort(key=lambda r: r.symbol)
    finished = time.time()
    report = DownloadReport(
        started_at=started_iso,
        finished_at=datetime.now(tz=timezone.utc).isoformat(),
        elapsed_s=round(finished - started, 2),
        results=results,
        root=str(historical_root()),
    )
    log.info(
        "download complete: created=%d updated=%d unchanged=%d errors=%d "
        "new_rows=%d elapsed=%.1fs",
        report.created, report.updated, report.unchanged,
        report.errors, report.total_new_rows, report.elapsed_s,
    )
    return report


# ---------------------------------------------------------------------- #
# Cache bridge — lets the unchanged backtest run on volume data         #
# ---------------------------------------------------------------------- #


def warm_cache_from_historical(symbols: Iterable[str]) -> int:
    """Populate ``data/cache/{SYMBOL}_daily.parquet`` from the historical store.

    The existing backtest path calls ``fetch_ohlcv`` which checks the
    short-lookback cache. By copying the historical parquet into that
    cache and touching the mtime to today, we make ``fetcher`` treat
    it as a fresh hit — no source code in the scanner / backtest needs
    to change. Returns the count of symbols actually copied.
    """
    cache_dir = get_settings().cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for sym in sorted({s.upper() for s in symbols}):
        src = symbol_path(sym)
        if not src.exists():
            continue
        dst = cache_dir / f"{sym}_daily.parquet"
        shutil.copyfile(src, dst)
        now = time.time()
        os.utime(dst, (now, now))
        copied += 1
    return copied


# ---------------------------------------------------------------------- #
# Internals                                                              #
# ---------------------------------------------------------------------- #


def _fetch_with_fallback(symbol: str, lookback_days: int) -> tuple[pd.DataFrame, str]:
    """Polygon-first, yfinance-fallback. Mirrors fetcher.py semantics but
    bypasses the short-lookback cache so the historical store stays the
    sole owner of long-history parquet files."""
    settings = get_settings()
    sources: list[str] = (
        ["polygon", "yfinance"] if settings.has_polygon else ["yfinance"]
    )
    last_error: Exception | None = None
    for source in sources:
        try:
            if source == "polygon":
                df = polygon_client.fetch_daily(symbol, lookback_days)
            else:
                df = yfinance_client.fetch_daily(symbol, lookback_days)
            return _normalize_frame(df), source
        except Exception as exc:  # noqa: BLE001
            log.debug("%s failed for %s: %s", source, symbol, exc)
            last_error = exc
    raise RuntimeError(f"all sources failed for {symbol}: {last_error}")


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Force the canonical shape: tz-naive ``date`` index, OHLCV columns, sorted."""
    if df.empty:
        return _empty_frame()
    out = df.copy()
    if out.index.name != "date":
        out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
        out.index.name = "date"
    else:
        out.index = pd.to_datetime(out.index)
        if out.index.tz is not None:
            out.index = out.index.tz_localize(None)
        out.index = out.index.normalize()
    cols = [c for c in OHLCV_COLUMNS if c in out.columns]
    out = out[cols]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _merge(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Concatenate two frames keeping the latest value on overlap."""
    combined = pd.concat([existing, fresh])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    return combined


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write to ``.tmp`` first then rename so a crashed write never leaves
    a half-baked parquet that a future read would fail on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp)
    os.replace(tmp, path)


def _empty_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=list(OHLCV_COLUMNS))
    df.index = pd.DatetimeIndex([], name="date")
    return df


# ---------------------------------------------------------------------- #
# CLI                                                                    #
# ---------------------------------------------------------------------- #


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.data.historical",
        description="Manage the durable OHLCV store.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # download
    dl = sub.add_parser("download", help="Download / refresh historical data.")
    group = dl.add_mutually_exclusive_group()
    group.add_argument("--symbols", help="Comma-separated tickers.")
    group.add_argument("--sector", nargs="+", metavar="SECTOR",
                       help="One or more sectors from src/config/sectors.py.")
    group.add_argument("--all", action="store_true",
                       help="Download the entire thematic universe.")
    dl.add_argument("--force", action="store_true",
                    help="Ignore existing files and re-pull full history.")
    dl.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS,
                    help=f"Parallel download workers (default {DEFAULT_MAX_WORKERS}).")
    dl.add_argument("--days", type=int, default=0,
                    help="Days of history to pull for new or forced symbols (default: 10 years). Pass e.g. 15000 for very deep history.")
    dl.add_argument("--json", action="store_true",
                    help="Print the full report as JSON to stdout.")

    # info
    info = sub.add_parser("info", help="Show stored stats for one symbol.")
    info.add_argument("symbol")

    # list
    sub.add_parser("list", help="Summarize the stored universe.")

    # clear
    sub.add_parser("clear", help="Delete ALL stored historical parquet files (respects STOCK_DATA_ROOT so it works on Modal volume when that env is active).")

    args = parser.parse_args(argv)

    if args.cmd == "download":
        return _cli_download(args)
    if args.cmd == "info":
        return _cli_info(args)
    if args.cmd == "list":
        return _cli_list()
    if args.cmd == "clear":
        return _cli_clear()
    return 2


def _cli_download(args: argparse.Namespace) -> int:
    symbols = _resolve_symbols(args)
    if not symbols:
        print("No symbols resolved. Pass --symbols, --sector, or --all.", file=sys.stderr)
        return 2

    report = download_universe(
        symbols,
        force=args.force,
        max_workers=args.workers,
        progress=True,
        lookback_days=args.days or None,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    print(f"\nHistorical store: {report.root}")
    print(f"  symbols processed: {len(report.results)}")
    print(f"  created: {report.created}   updated: {report.updated}   "
          f"unchanged: {report.unchanged}   errors: {report.errors}")
    print(f"  new rows written: {report.total_new_rows}")
    print(f"  elapsed: {report.elapsed_s:.1f}s")
    if report.errors:
        print("\nFailures:")
        for r in report.results:
            if r.status == "error":
                print(f"  {r.symbol}: {r.error}")
    return 0 if report.errors == 0 else 1


def _cli_info(args: argparse.Namespace) -> int:
    sym = args.symbol.upper()
    df = load_history(sym)
    if df.empty:
        print(f"{sym}: not stored")
        return 1
    print(f"{sym}")
    print(f"  rows:        {len(df)}")
    print(f"  first bar:   {df.index.min().date()}")
    print(f"  last bar:    {df.index.max().date()}")
    print(f"  path:        {symbol_path(sym)}")
    return 0


def _cli_list() -> int:
    root = historical_root()
    files = sorted(root.glob("*.parquet"))
    if not files:
        print(f"{root}: empty")
        return 0
    print(f"{root}: {len(files)} symbols")
    for f in files[:50]:
        size_kb = f.stat().st_size / 1024
        print(f"  {f.stem:<10} {size_kb:>8.1f} KB")
    if len(files) > 50:
        print(f"  … and {len(files) - 50} more")
    return 0


def _cli_clear() -> int:
    """Delete every parquet in the active historical root (local or Modal via env)."""
    root = historical_root()
    files = sorted(root.glob("*.parquet"))
    removed = 0
    for f in files:
        f.unlink()
        removed += 1
    print(f"Cleared {removed} historical files from {root}")
    if removed == 0:
        print("  (nothing to do)")
    return 0
    return 0


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return sorted({s.strip().upper() for s in args.symbols.split(",") if s.strip()})
    if args.sector:
        return tickers_for_sectors(args.sector)
    if args.all:
        return all_tickers()
    return []


if __name__ == "__main__":
    sys.exit(_cli())
