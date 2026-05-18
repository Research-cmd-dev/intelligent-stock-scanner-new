"""Cross-sectional Information Coefficient (IC) evaluation.

For each feature we compute the Spearman rank correlation between the
feature value at time *t* and the *N*-day forward return at *t*,
across all symbols available on that date. That per-date IC is then
aggregated over the whole window:

    mean_ic   — average daily IC
    std_ic    — std dev of daily IC
    ir        — mean_ic / std_ic (information ratio, unitless)
    t_stat    — mean_ic / (std_ic / √N) (significance proxy)
    n_periods — number of dates with a usable cross section
    abs_ic    — |mean_ic|, used to rank features by raw predictive power

A feature with an ``|IR| > 1.0`` over enough periods is a candidate
worth investigating further; below ~0.5 it's noise. These thresholds
are codified in :mod:`src.backtest.refine` rather than here so the
evaluation stays a pure measurement.

We require at least :data:`MIN_SYMBOLS_PER_DATE` symbols per date to
compute a daily IC — Spearman on 2–3 points is not informative.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.utils import get_logger

log = get_logger(__name__)


# Below this many symbols on a given date we don't even try to compute
# the IC — the rank correlation is too noisy to be load-bearing.
MIN_SYMBOLS_PER_DATE = 5

# A feature is excluded from the report if it has fewer than this many
# non-null (feature, return) pairs across the entire window.
MIN_OBSERVATIONS = 50


@dataclass(frozen=True)
class FeatureStats:
    """Per-feature aggregate IC statistics."""

    name: str
    category: str            # "alpha158" | "custom" | "scanner" | "narrative" | "sector"
    mean_ic: float
    std_ic: float
    ir: float
    t_stat: float
    n_periods: int           # dates with a usable cross section
    n_observations: int      # total (feature, return) pairs

    @property
    def abs_ir(self) -> float:
        return abs(self.ir)

    def to_row(self) -> dict[str, object]:
        return {
            "feature": self.name,
            "category": self.category,
            "mean_ic": round(self.mean_ic, 4),
            "std_ic": round(self.std_ic, 4),
            "ir": round(self.ir, 3),
            "t_stat": round(self.t_stat, 2),
            "n_periods": self.n_periods,
            "n_obs": self.n_observations,
        }


@dataclass(frozen=True)
class FeatureEvaluation:
    """Result of evaluating a feature panel against forward returns.

    ``stats`` is sorted by descending ``abs_ir`` so the most-predictive
    features (in either direction) appear first.
    """

    forward_horizon: int                       # bars used for the forward return
    stats: list[FeatureStats]
    daily_ic: pd.DataFrame = field(default_factory=pd.DataFrame)  # rows=date, cols=feature
    category_map: dict[str, str] = field(default_factory=dict)

    def to_dataframe(self) -> pd.DataFrame:
        if not self.stats:
            return pd.DataFrame()
        return pd.DataFrame([s.to_row() for s in self.stats])


# ---------------------------------------------------------------------- #
# Public entry point                                                     #
# ---------------------------------------------------------------------- #


def evaluate_features(
    panel: pd.DataFrame,
    close_panel: pd.DataFrame,
    *,
    forward_horizon: int = 5,
    category_map: dict[str, str] | None = None,
) -> FeatureEvaluation:
    """Compute IC stats for every feature column in ``panel``.

    Args:
        panel: Long-form features, MultiIndex ``(symbol, date)``,
            columns are feature names.
        close_panel: Wide-form close prices, columns=symbols,
            index=date — used to compute forward returns.
        forward_horizon: Bars ahead for the forward-return target.
            5 = roughly one trading week; matches the rule-of-thumb
            "does this signal predict next-week direction?"
        category_map: Optional ``feature_name → category`` mapping
            used purely for breakdown / refinement reads.

    The function silently drops any feature with fewer than
    :data:`MIN_OBSERVATIONS` usable rows. Returns a
    :class:`FeatureEvaluation` with ``stats`` sorted by ``|IR|`` desc.
    """
    if panel.empty or close_panel.empty:
        return FeatureEvaluation(
            forward_horizon=forward_horizon,
            stats=[],
            category_map=category_map or {},
        )

    fwd = _forward_return_panel(close_panel, horizon=forward_horizon)

    # Align target onto panel's (symbol, date) index, dropping rows
    # where the forward return is unknown (end of window). Explicit
    # index names are required for the MultiIndex join below.
    fwd.index.name = "date"
    fwd.columns.name = "symbol"
    fwd_long = fwd.stack(future_stack=True).swaplevel(0, 1).sort_index()
    fwd_long.name = "_fwd"
    fwd_long.index.names = ["symbol", "date"]
    panel = panel.join(fwd_long, how="left")

    daily_ic_by_feature: dict[str, pd.Series] = {}
    stats: list[FeatureStats] = []

    feature_cols = [c for c in panel.columns if c != "_fwd"]
    category_map = category_map or {}

    for feature in feature_cols:
        ic_series = _daily_ic_for(panel, feature)
        if ic_series is None:
            continue
        n_obs = panel[feature].notna().sum()
        if n_obs < MIN_OBSERVATIONS or ic_series.empty:
            continue
        daily_ic_by_feature[feature] = ic_series

        mean = float(ic_series.mean())
        std = float(ic_series.std()) if len(ic_series) > 1 else 0.0
        ir = mean / std if std > 0 else 0.0
        t_stat = mean / (std / np.sqrt(len(ic_series))) if std > 0 else 0.0

        stats.append(FeatureStats(
            name=feature,
            category=category_map.get(feature, "unknown"),
            mean_ic=mean,
            std_ic=std,
            ir=ir,
            t_stat=t_stat,
            n_periods=len(ic_series),
            n_observations=int(n_obs),
        ))

    stats.sort(key=lambda s: s.abs_ir, reverse=True)

    daily_ic = (
        pd.DataFrame(daily_ic_by_feature)
        if daily_ic_by_feature else pd.DataFrame()
    )
    return FeatureEvaluation(
        forward_horizon=forward_horizon,
        stats=stats,
        daily_ic=daily_ic,
        category_map=category_map,
    )


# ---------------------------------------------------------------------- #
# Internals                                                              #
# ---------------------------------------------------------------------- #


def _forward_return_panel(close: pd.DataFrame, *, horizon: int) -> pd.DataFrame:
    """Forward return on each symbol over ``horizon`` bars."""
    return close.shift(-horizon) / close - 1.0


def _daily_ic_for(panel: pd.DataFrame, feature: str) -> pd.Series | None:
    """Compute one Spearman rank correlation per date.

    Dates with fewer than :data:`MIN_SYMBOLS_PER_DATE` valid pairs are
    skipped. Returns ``None`` if no usable dates exist.
    """
    sub = panel[[feature, "_fwd"]].dropna()
    if sub.empty:
        return None

    # Group by date (level=1 in the (symbol, date) MultiIndex). For each
    # date, rank both columns and compute Pearson on the ranks — that's
    # Spearman.
    def _spearman(group: pd.DataFrame) -> float:
        if len(group) < MIN_SYMBOLS_PER_DATE:
            return np.nan
        x = group[feature].rank()
        y = group["_fwd"].rank()
        if x.std() == 0 or y.std() == 0:
            return np.nan
        return float(x.corr(y))

    by_date = sub.groupby(level=1).apply(_spearman)
    by_date = by_date.dropna()
    if by_date.empty:
        return None
    return by_date
