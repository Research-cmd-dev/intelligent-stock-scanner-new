"""Scan orchestration.

The :class:`Scanner` class is the public interface for running a scan over
a set of symbols. It owns three responsibilities:

    1. **Universe** — resolve which symbols to fetch (full discovery,
       sector slice, or explicit watchlist).
    2. **Fetch + enrich** — pull OHLCV via the unified fetcher and attach
       indicator columns. Symbols whose data fetch fails are dropped with
       a warning, not an exception, so a flaky single name never aborts
       the run.
    3. **Detect + rank** — run every registered detector against each
       enriched frame, collect :class:`MatchResult` hits, and sort by
       score descending.

Side-effect-free apart from disk cache writes inside the fetcher; safe to
call from a Streamlit ``@st.cache_data`` boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.data import fetch_many, fetch_ohlcv
from src.utils import get_logger

from .indicators import add_indicators, has_min_history
from .patterns import ALL_DETECTORS, Detector, MatchResult
from .universe import build_universe, classify

log = get_logger(__name__)


@dataclass
class ScanReport:
    """Outcome of a single scan run.

    ``matches`` are sorted by score descending. ``coverage`` records which
    symbols were attempted and which produced usable data, so the dashboard
    can show "scanned 142, fetched 138, 11 hits" honestly.
    """

    matches: list[MatchResult]
    coverage: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dataframe(self) -> pd.DataFrame:
        """Flat table of all matches, ready for display."""
        if not self.matches:
            return pd.DataFrame()
        return pd.DataFrame([m.to_row() for m in self.matches])

    def by_pattern(self, pattern: str) -> list[MatchResult]:
        """Filter matches by detector name."""
        return [m for m in self.matches if m.pattern == pattern]


class Scanner:
    """Top-level orchestrator. Re-usable across runs; holds no per-run state."""

    def __init__(
        self,
        detectors: tuple[tuple[str, Detector], ...] | None = None,
        *,
        min_score: float = 50.0,
        lookback_days: int = 300,
    ) -> None:
        """
        Args:
            detectors: Override the default detector registry. Each entry
                is ``(name, callable)``. Defaults to all registered patterns.
            min_score: Drop matches whose score is below this threshold.
                Set to 0 to keep everything.
            lookback_days: How many calendar days of history to request per
                symbol. Must comfortably cover the longest indicator
                lookback (SMA200 → at least ~300 calendar days).
        """
        self.detectors = detectors if detectors is not None else ALL_DETECTORS
        self.min_score = min_score
        self.lookback_days = lookback_days

    # ------------------------------------------------------------------ #
    # Public entry points                                                #
    # ------------------------------------------------------------------ #

    def scan_watchlist(self, symbols: list[str]) -> ScanReport:
        """Scan an explicit list of symbols. No universe construction."""
        if not symbols:
            return ScanReport(matches=[])
        return self._scan(sorted({s.upper() for s in symbols}))

    def scan_discovery(
        self,
        *,
        sectors: list[str] | None = None,
        include_sector_etfs: bool = True,
        include_theme_etfs: bool = True,
        include_broad_market: bool = False,
        extra: list[str] | None = None,
    ) -> ScanReport:
        """Full thematic discovery scan.

        Builds the universe from :func:`src.scanner.universe.build_universe`
        and runs every detector across it.
        """
        universe = build_universe(
            sectors=sectors,
            include_sector_etfs=include_sector_etfs,
            include_theme_etfs=include_theme_etfs,
            include_broad_market=include_broad_market,
            extra=extra,
        )
        log.info("discovery scan universe: %d symbols", len(universe))
        return self._scan(universe)

    def scan_frame(
        self, df: pd.DataFrame, symbol: str
    ) -> list[MatchResult]:
        """Run all detectors against a single pre-fetched frame.

        Useful for tests and for the dashboard's "re-evaluate one symbol"
        path. ``df`` may or may not already have indicator columns; we
        add them if missing.
        """
        enriched = df if "rsi14" in df.columns else add_indicators(df)
        return self._evaluate(enriched, symbol)

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _scan(self, symbols: list[str]) -> ScanReport:
        frames = fetch_many(symbols, lookback_days=self.lookback_days)
        matches: list[MatchResult] = []
        errors: dict[str, str] = {
            s: "fetch failed" for s in symbols if s not in frames
        }

        for symbol, df in frames.items():
            try:
                enriched = add_indicators(df)
            except Exception as exc:
                log.warning("indicator failure on %s: %s", symbol, exc)
                errors[symbol] = f"indicator error: {exc}"
                continue
            matches.extend(self._evaluate(enriched, symbol))

        matches = [m for m in matches if m.score >= self.min_score]
        matches.sort(key=lambda m: m.score, reverse=True)

        coverage = {
            "universe": len(symbols),
            "fetched": len(frames),
            "matches": len(matches),
        }
        log.info(
            "scan complete: %d universe → %d fetched → %d matches",
            coverage["universe"], coverage["fetched"], coverage["matches"],
        )
        return ScanReport(matches=matches, coverage=coverage, errors=errors)

    def _evaluate(self, df: pd.DataFrame, symbol: str) -> list[MatchResult]:
        if not has_min_history(df, 220):
            return []
        out: list[MatchResult] = []
        tags = classify(symbol)
        sectors = tuple(tags["sectors"])
        themes = tuple(tags["themes"])
        for name, detector in self.detectors:
            try:
                hit = detector(df, symbol)
            except Exception as exc:
                log.warning("%s failed on %s: %s", name, symbol, exc)
                continue
            if hit is None:
                continue
            # Enrich with orchestrator-level metadata the detector can't know.
            enriched = MatchResult(
                symbol=hit.symbol,
                pattern=hit.pattern,
                score=hit.score,
                as_of=hit.as_of,
                price=hit.price,
                sectors=sectors,
                themes=themes,
                source=hit.source,
                indicators=hit.indicators,
                factors=hit.factors,
            )
            out.append(enriched)
        return out


# ---------------------------------------------------------------------- #
# Module-level convenience                                               #
# ---------------------------------------------------------------------- #


def run_scan(
    symbols: list[str] | None = None,
    *,
    sectors: list[str] | None = None,
    min_score: float = 50.0,
    include_etfs: bool = True,
    include_broad_market: bool = False,
) -> ScanReport:
    """One-call helper used by the dashboard and ad-hoc scripts.

    Pass ``symbols`` for a watchlist scan; omit it for a discovery scan
    constrained by ``sectors`` (or all sectors if that is also omitted).
    """
    scanner = Scanner(min_score=min_score)
    if symbols:
        return scanner.scan_watchlist(symbols)
    return scanner.scan_discovery(
        sectors=sectors,
        include_sector_etfs=include_etfs,
        include_theme_etfs=include_etfs,
        include_broad_market=include_broad_market,
    )


__all__ = ["ScanReport", "Scanner", "MatchResult", "run_scan"]
