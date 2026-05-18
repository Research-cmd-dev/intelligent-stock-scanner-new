"""End-to-end feature evaluation pipeline.

Composes the three layers:

1. Per-symbol fetch + indicator enrichment (re-uses the scanner stack
   so any new indicator immediately becomes available as a feature
   input).
2. Per-symbol feature computation: Alpha158-lite + custom.
3. Panel assembly + sector-relative features + IC evaluation.

One public entry: :func:`build_feature_evaluation`. The backtest layer
calls this from :func:`src.backtest.run_backtest` when feature
evaluation is enabled; tests and ad-hoc scripts can call it directly.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from src.data import fetch_ohlcv
from src.scanner.indicators import add_indicators, has_min_history
from src.utils import get_logger

from .alpha158 import ALPHA158_LITE_FEATURES, compute_alpha158_lite
from .custom import (
    CUSTOM_FEATURES,
    add_narrative,
    add_sector_relative,
    compute_per_symbol,
)
from .evaluator import FeatureEvaluation, evaluate_features

log = get_logger(__name__)


# Bars of history fetched before ``start`` so the longest feature
# (MA60, ROC60) has a warmed-up window.
WARMUP_DAYS = 400


def build_feature_evaluation(
    symbols: Iterable[str],
    *,
    start: str | date | pd.Timestamp,
    end: str | date | pd.Timestamp,
    forward_horizon: int = 5,
    narrative_scores: dict[str, float] | None = None,
) -> FeatureEvaluation:
    """Compute per-feature IC stats across ``symbols``.

    Args:
        symbols: Universe to evaluate over.
        start, end: In-window dates. Features need warmup history so
            we always fetch ``WARMUP_DAYS`` past ``start``.
        forward_horizon: Bars ahead for the return target. 5 trading
            days ≈ 1 week, matching the rule-of-thumb evaluation horizon.
        narrative_scores: Optional ``symbol → 0..1 narrative score``
            map. Broadcast onto the most recent bar per symbol so the
            "is today's narrative predictive?" question can be asked
            even when historical news isn't available.

    Returns:
        A :class:`FeatureEvaluation`. ``stats`` is sorted by ``|IR|``
        descending; empty when no symbol produced enough history.
    """
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if end_ts < start_ts:
        raise ValueError("end must be on or after start")

    lookback = (end_ts - start_ts).days + WARMUP_DAYS + 30
    panel_frames: list[pd.DataFrame] = []
    close_columns: dict[str, pd.Series] = {}

    for symbol in sorted({s.upper() for s in symbols}):
        try:
            raw = fetch_ohlcv(symbol, lookback_days=lookback)
        except Exception as exc:
            log.warning("fetch failed for %s during feature build: %s", symbol, exc)
            continue
        if not has_min_history(raw, 220):
            log.info("skipping %s: insufficient history for features", symbol)
            continue

        enriched = add_indicators(raw)
        per_symbol = pd.concat(
            [compute_alpha158_lite(enriched), compute_per_symbol(enriched)],
            axis=1,
        )
        # Restrict to the in-window dates before we panel-stack so the
        # long form stays compact.
        per_symbol = per_symbol.loc[(per_symbol.index >= start_ts) & (per_symbol.index <= end_ts)]
        if per_symbol.empty:
            continue
        per_symbol.index = pd.MultiIndex.from_product(
            [[symbol], per_symbol.index], names=["symbol", "date"],
        )
        panel_frames.append(per_symbol)

        # Wide-form close for sector-relative and forward-return computations.
        close_columns[symbol] = enriched["close"].loc[
            (enriched.index >= start_ts - pd.Timedelta(days=60)) & (enriched.index <= end_ts)
        ]

    if not panel_frames:
        log.info("feature evaluation: no symbols produced usable history")
        return FeatureEvaluation(forward_horizon=forward_horizon, stats=[])

    panel = pd.concat(panel_frames).sort_index()
    close_panel = pd.DataFrame(close_columns).sort_index()

    panel = add_sector_relative(panel, close_panel=close_panel)
    panel = add_narrative(panel, narrative_scores=narrative_scores)

    category_map = _category_map()
    return evaluate_features(
        panel,
        close_panel,
        forward_horizon=forward_horizon,
        category_map=category_map,
    )


def _category_map() -> dict[str, str]:
    """Tag every feature with its source so the report can group them."""
    out: dict[str, str] = {}
    for name in ALPHA158_LITE_FEATURES:
        out[name] = "alpha158"
    for name in CUSTOM_FEATURES:
        if name.startswith("SECTOR_"):
            out[name] = "sector"
        elif name.startswith("NARRATIVE"):
            out[name] = "narrative"
        else:
            out[name] = "scanner"
    return out
