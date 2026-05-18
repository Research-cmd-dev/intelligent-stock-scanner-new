"""Custom features specific to the Stock Finder Agent.

Three families:

- **Scanner-derived** — features that reuse scanner indicators
  (RSI, distance-from-MA, drawdown) but reshape them into forms the
  feature evaluator can score in a cross-section: rolling z-scores,
  binary signal flags, percentile ranks.

- **Sector-relative** — same-day comparisons against a symbol's
  sector cohort. The most useful feature in this family is
  ``sector_relative_5d_return`` — a symbol's 5-day return minus the
  median 5-day return across its sector — which captures "leading the
  group" vs "lagging the group."

- **Narrative** — populated when historical news is available.
  yfinance has no backfill so narrative features only exist for the
  most recent ~14 days. The evaluator simply drops missing rows, so a
  symbol without history just contributes nothing to the narrative
  feature IC — better than silently zero-filling.

All features are computed on the indicator-augmented frame produced by
:func:`src.scanner.indicators.add_indicators`, so detectors and
features see the exact same indicator values.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from src.config.sectors import sector_for
from src.utils import get_logger

log = get_logger(__name__)


CUSTOM_FEATURES: tuple[str, ...] = (
    "RSI14_Z60",                 # 60-day z-score of RSI14
    "DIST_SMA50_Z60",            # 60-day z-score of distance-from-SMA50
    "DIST_SMA200_Z60",           # 60-day z-score of distance-from-SMA200
    "DRAWDOWN_120",              # raw drawdown from trailing 120-bar high
    "DRAWDOWN_120_Z60",          # 60-day z-score of the above
    "TREND_REGIME",              # +1 if SMA50>SMA200 and rising, −1 if both falling, 0 else
    "PULLBACK_DEPTH",            # max(0, dist below SMA50) — pullback intensity
    "SECTOR_RELATIVE_5D",        # symbol 5D return − sector-median 5D return
    "SECTOR_RELATIVE_20D",       # symbol 20D return − sector-median 20D return
    "NARRATIVE_SCORE",           # 0-1 narrative score (NaN when news unavailable)
)


# ---------------------------------------------------------------------- #
# Per-symbol features (no cross-sectional dependency)                    #
# ---------------------------------------------------------------------- #


def compute_per_symbol(df: pd.DataFrame) -> pd.DataFrame:
    """Compute features that only need a single symbol's enriched frame.

    Sector-relative and narrative features are added later by the
    pipeline because they need a panel and an external scorer.
    """
    out = pd.DataFrame(index=df.index)

    rsi = df.get("rsi14")
    if rsi is not None:
        out["RSI14_Z60"] = _rolling_zscore(rsi, window=60)

    dist50 = df.get("dist_sma50")
    if dist50 is not None:
        out["DIST_SMA50_Z60"] = _rolling_zscore(dist50, window=60)
        # Pullback depth: how far below SMA50 are we, in percent? Zero
        # when above, positive when below. Captures "the deeper the
        # better up to a point" semantics from Bottom Hunter.
        out["PULLBACK_DEPTH"] = (-dist50).clip(lower=0)

    dist200 = df.get("dist_sma200")
    if dist200 is not None:
        out["DIST_SMA200_Z60"] = _rolling_zscore(dist200, window=60)

    dd = df.get("drawdown_120")
    if dd is not None:
        out["DRAWDOWN_120"] = dd
        out["DRAWDOWN_120_Z60"] = _rolling_zscore(dd, window=60)

    sma50 = df.get("sma50")
    sma200 = df.get("sma200")
    sma200_slope = df.get("sma200_slope_20")
    sma50_slope = df.get("sma50_slope_20")
    if all(s is not None for s in (sma50, sma200, sma200_slope, sma50_slope)):
        regime = np.where(
            (sma50 > sma200) & (sma200_slope > 0), 1.0,
            np.where((sma50 < sma200) & (sma200_slope < 0), -1.0, 0.0),
        )
        out["TREND_REGIME"] = pd.Series(regime, index=df.index)

    return out


# ---------------------------------------------------------------------- #
# Panel-level features (need the whole universe)                         #
# ---------------------------------------------------------------------- #


def add_sector_relative(
    panel: pd.DataFrame,
    *,
    close_panel: pd.DataFrame,
) -> pd.DataFrame:
    """Add sector-relative return features in place and return the panel.

    Args:
        panel: Long-form feature panel with a (symbol, date) MultiIndex.
        close_panel: Wide-form close prices, columns=symbols, index=date.
            Used to compute the per-symbol returns we then de-mean by
            sector cohort.

    The returns themselves are deliberately re-computed here rather than
    pulled from the per-symbol feature frame: a symbol's sector
    membership comes from :func:`src.config.sectors.sector_for`, which
    works off the wide panel's columns.
    """
    if close_panel.empty:
        return panel

    symbols = list(close_panel.columns)
    # Map symbol → first sector (most have one; a few have multiple — we
    # take the first deterministically so the breakdown is stable run to run).
    sector_map = {}
    for sym in symbols:
        sectors = sector_for(sym)
        if sectors:
            sector_map[sym] = sectors[0]

    for horizon in (5, 20):
        ret = close_panel.pct_change(horizon)
        # Compute the sector median return at each date, then broadcast
        # back to each symbol so we can subtract.
        sector_returns: dict[str, pd.Series] = {}
        for sector in set(sector_map.values()):
            cohort = [s for s, sec in sector_map.items() if sec == sector]
            if len(cohort) < 2:
                continue
            sector_returns[sector] = ret[cohort].median(axis=1)

        per_symbol_sector_ret = pd.DataFrame(index=ret.index, columns=symbols)
        for sym in symbols:
            sector = sector_map.get(sym)
            if sector and sector in sector_returns:
                per_symbol_sector_ret[sym] = sector_returns[sector]

        diff = ret - per_symbol_sector_ret
        # Stack to long form (symbol, date) and align onto the panel index.
        # Explicit index names matter — without them the join finds no
        # overlapping levels and falls over.
        diff.index.name = "date"
        diff.columns.name = "symbol"
        stacked = diff.stack(future_stack=True).swaplevel(0, 1).sort_index()
        stacked.name = f"SECTOR_RELATIVE_{horizon}D"
        stacked.index.names = ["symbol", "date"]
        panel = panel.join(stacked.to_frame(), how="left")

    return panel


def add_narrative(
    panel: pd.DataFrame,
    *,
    narrative_scores: dict[str, float] | None,
) -> pd.DataFrame:
    """Attach a per-symbol narrative score as a constant feature column.

    ``narrative_scores`` is the live snapshot from
    :class:`src.narrative.NarrativeScorer`. We broadcast each value to
    the last bar only, since yfinance has no historical news backfill;
    everything else stays NaN and the evaluator drops it. This makes
    the narrative feature useful for *live* feature evaluation runs
    without forcing every backtest to do a heavy historical news pull.
    """
    if not narrative_scores:
        # Still add the column so downstream code can rely on its
        # presence; evaluator will skip features whose non-null count
        # is too small.
        panel["NARRATIVE_SCORE"] = np.nan
        return panel

    # The panel's outer level is symbol, inner is date. We assign the
    # score to the most recent (max) date per symbol.
    col = pd.Series(index=panel.index, dtype=float)
    for symbol, score in narrative_scores.items():
        if symbol not in panel.index.get_level_values(0):
            continue
        dates = panel.loc[symbol].index
        if len(dates) == 0:
            continue
        col.loc[(symbol, dates.max())] = score
    panel["NARRATIVE_SCORE"] = col
    return panel


# ---------------------------------------------------------------------- #
# Helpers                                                                #
# ---------------------------------------------------------------------- #


def _rolling_zscore(series: pd.Series, *, window: int) -> pd.Series:
    """``(x − mean) / std`` over the trailing window. Min-period == window."""
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    return (series - mean) / std.replace(0, np.nan)


def known_feature_names() -> Iterable[str]:
    """Iterate every feature name this module is *expected* to produce.

    Useful for the evaluator's stable column ordering and for
    refinement heuristics that want to list features by source.
    """
    return CUSTOM_FEATURES
