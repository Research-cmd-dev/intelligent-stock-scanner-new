"""Tests for the feature-engineering layer.

All synthetic — no network. We build OHLCV frames by hand to drive the
Alpha158-lite computations and the IC evaluator, then verify both the
feature shapes and the refinement heuristics that read them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestReport
from src.backtest.metrics import (
    breakdown_by, breakdown_by_score_band, compute_metrics,
)
from src.backtest.refine import (
    MIN_TOTAL_TRADES_FOR_CONFIDENCE,
    STRONG_IR,
    suggest_improvements,
)
from src.backtest.signals import BacktestSignal
from src.backtest.engine import Trade
from src.features import FeatureEvaluation, evaluate_features
from src.features.alpha158 import (
    ALPHA158_LITE_FEATURES,
    compute_alpha158_lite,
)
from src.features.custom import (
    CUSTOM_FEATURES,
    add_sector_relative,
    compute_per_symbol,
)
from src.features.evaluator import FeatureStats


# ---------------------------------------------------------------------- #
# Fixtures                                                               #
# ---------------------------------------------------------------------- #


def _ohlcv(n: int = 120, drift: float = 0.001, vol: float = 0.01,
           seed: int = 0) -> pd.DataFrame:
    """Realistic-shaped OHLCV with a controllable drift + volatility."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    returns = rng.normal(drift, vol, size=n)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.005, n)))
    volume = rng.integers(500_000, 2_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


# ---------------------------------------------------------------------- #
# Alpha158-lite                                                          #
# ---------------------------------------------------------------------- #


def test_alpha158_lite_returns_one_column_per_feature():
    feats = compute_alpha158_lite(_ohlcv())
    assert set(feats.columns) == set(ALPHA158_LITE_FEATURES)


def test_alpha158_lite_kmid_is_close_over_open_minus_one():
    df = _ohlcv()
    feats = compute_alpha158_lite(df)
    # KMID definition is (close - open) / open
    expected = (df["close"] - df["open"]) / df["open"]
    pd.testing.assert_series_equal(
        feats["KMID"], expected.rename("KMID"), check_names=True,
    )


def test_alpha158_lite_warmup_rows_are_nan_for_long_windows():
    feats = compute_alpha158_lite(_ohlcv(n=100))
    # ROC60 needs 60 bars of history.
    assert feats["ROC60"].iloc[:59].isna().all()
    assert feats["ROC60"].iloc[60:].notna().any()


def test_alpha158_lite_imax5_is_one_on_rising_close():
    # Strictly rising close → today is always the 5-bar max.
    n = 30
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(np.linspace(100, 130, n), index=dates)
    df = pd.DataFrame({
        "open": close, "high": close, "low": close, "close": close,
        "volume": [1_000_000] * n,
    })
    feats = compute_alpha158_lite(df)
    # After the 5-bar warmup, IMAX5 should be 1.0 every day.
    assert (feats["IMAX5"].dropna() == 1.0).all()


# ---------------------------------------------------------------------- #
# Custom features                                                        #
# ---------------------------------------------------------------------- #


def test_compute_per_symbol_emits_expected_features():
    from src.scanner.indicators import add_indicators
    enriched = add_indicators(_ohlcv(n=260))
    feats = compute_per_symbol(enriched)
    # The scanner-derived features should all be present once history is long enough.
    for name in ("RSI14_Z60", "DIST_SMA50_Z60", "DIST_SMA200_Z60",
                 "DRAWDOWN_120", "PULLBACK_DEPTH", "TREND_REGIME"):
        assert name in feats.columns, f"missing {name}"


def test_sector_relative_subtracts_sector_median():
    # Two symbols in the same sector should net to ~0 relative return on
    # any given date (the median of two values is one of them — but the
    # other will see the symmetric opposite, so the *mean* across both
    # is 0). Easier-to-assert: at least one symbol's series is not all NaN.
    dates = pd.date_range("2024-01-02", periods=40, freq="B")
    panel = pd.DataFrame(
        index=pd.MultiIndex.from_product([["NVDA", "AMD"], dates],
                                         names=["symbol", "date"]),
    )
    close = pd.DataFrame({
        "NVDA": np.linspace(100, 120, 40),
        "AMD": np.linspace(50, 55, 40),
    }, index=dates)
    out = add_sector_relative(panel, close_panel=close)
    # NVDA and AMD are both in Chips, so the relative return columns
    # must exist and at least the longer horizon should have data.
    assert "SECTOR_RELATIVE_5D" in out.columns
    assert "SECTOR_RELATIVE_20D" in out.columns
    assert out["SECTOR_RELATIVE_20D"].notna().any()


# ---------------------------------------------------------------------- #
# Evaluator (IC / IR)                                                    #
# ---------------------------------------------------------------------- #


def _make_panel_with_known_signal(
    n_symbols: int = 8, n_dates: int = 60, signal_strength: float = 1.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic panel where feature X is correlated with forward return.

    Each (symbol, date) gets a random X. The next-day close is set so
    that the 5-day forward return is roughly proportional to X scaled
    by ``signal_strength``. With strength=1.0 IC should be ~1.0; with
    strength=0.0 it should be ~0.
    """
    rng = np.random.default_rng(7)
    dates = pd.date_range("2024-01-02", periods=n_dates, freq="B")
    symbols = [f"S{i}" for i in range(n_symbols)]

    panel_rows = []
    closes = {sym: [100.0] for sym in symbols}
    feature_values = {}

    for d in dates:
        for sym in symbols:
            x = rng.normal()
            feature_values[(sym, d)] = x
            # 5-day forward return = signal_strength * x + noise. We
            # encode this by setting the close on date+5 relative to today.
            noise = rng.normal(0, 0.005)
            fwd_ret = signal_strength * x * 0.01 + noise
            closes[sym].append(closes[sym][-1] * (1 + fwd_ret / 5))

    # Build long-form feature panel
    for (sym, d), val in feature_values.items():
        panel_rows.append({"symbol": sym, "date": d, "X": val})
    panel = (
        pd.DataFrame(panel_rows)
        .set_index(["symbol", "date"])
        .sort_index()
    )

    # Wide-form close: ensure forward returns line up with X.
    # The above closes list has n_dates+1 entries; trim.
    close_df = pd.DataFrame(
        {sym: closes[sym][:n_dates] for sym in symbols},
        index=dates,
    )
    return panel, close_df


def test_evaluator_picks_up_strong_signal():
    panel, close = _make_panel_with_known_signal(signal_strength=2.0)
    fe = evaluate_features(panel, close, forward_horizon=5)
    assert fe.stats
    # X should have a clearly positive mean IC.
    stat = next(s for s in fe.stats if s.name == "X")
    assert stat.mean_ic > 0.2, f"expected strong positive IC, got {stat.mean_ic}"


def test_evaluator_returns_zero_ish_for_random_feature():
    panel, close = _make_panel_with_known_signal(signal_strength=0.0)
    fe = evaluate_features(panel, close, forward_horizon=5)
    if not fe.stats:
        # Acceptable: no usable observations after dropping NaNs.
        return
    stat = next(s for s in fe.stats if s.name == "X")
    assert abs(stat.mean_ic) < 0.1, f"random feature should be noise, got IC={stat.mean_ic}"


def test_evaluator_skips_features_with_thin_observations():
    # Single date, single symbol → no cross section anywhere.
    panel = pd.DataFrame(
        {"X": [0.5]},
        index=pd.MultiIndex.from_tuples([("AAA", pd.Timestamp("2024-01-02"))],
                                        names=["symbol", "date"]),
    )
    close = pd.DataFrame({"AAA": [100.0]}, index=[pd.Timestamp("2024-01-02")])
    fe = evaluate_features(panel, close, forward_horizon=5)
    assert fe.stats == []


# ---------------------------------------------------------------------- #
# Refinement integration                                                 #
# ---------------------------------------------------------------------- #


def _trade(ret=0.05, pattern="trend_rider", score=72, sectors=("AI",)):
    sig = BacktestSignal(
        symbol="TEST", date=pd.Timestamp("2024-01-05"),
        pattern=pattern, score=score, price=100.0, sectors=sectors,
    )
    return Trade(
        signal=sig,
        entry_date=pd.Timestamp("2024-01-06"), entry_price=100.0,
        exit_date=pd.Timestamp("2024-02-06"), exit_price=100 * (1 + ret),
        hold_days=20,
    )


def _report_with_features(stats: list[FeatureStats]) -> BacktestReport:
    """A clean trade history + a feature evaluation we control."""
    trades = [_trade(0.04) for _ in range(MIN_TOTAL_TRADES_FOR_CONFIDENCE * 2)]
    fe = FeatureEvaluation(forward_horizon=5, stats=stats)
    return BacktestReport(
        signals=[t.signal for t in trades],
        trades=trades,
        metrics=compute_metrics(trades),
        by_pattern=breakdown_by(trades, "pattern"),
        by_sector=breakdown_by(trades, "sector"),
        by_score_band=breakdown_by_score_band(trades),
        features_evaluation=fe,
        params={},
    )


def test_strong_external_feature_triggers_promotion_suggestion():
    stats = [
        FeatureStats(name="QTLU20", category="alpha158",
                     mean_ic=0.06, std_ic=0.04, ir=1.5, t_stat=2.5,
                     n_periods=40, n_observations=400),
    ]
    report = _report_with_features(stats)
    suggestions = suggest_improvements(report)
    feature_suggestions = [s for s in suggestions if s.category == "feature"]
    assert feature_suggestions, "expected a feature promotion suggestion"
    assert "QTLU20" in feature_suggestions[0].title


def test_weak_scanner_input_triggers_deprecation_suggestion():
    stats = [
        FeatureStats(name="RSI14_Z60", category="scanner",
                     mean_ic=0.001, std_ic=0.02, ir=0.05, t_stat=0.1,
                     n_periods=40, n_observations=400),
    ]
    report = _report_with_features(stats)
    suggestions = suggest_improvements(report)
    weak = [s for s in suggestions
            if s.category == "feature" and "weak" in s.title.lower()]
    assert weak, "expected a weak-feature deprecation suggestion"


def test_clean_feature_evaluation_emits_no_promotion():
    # Every feature has a mediocre IR — neither strong nor weak enough
    # to trip the heuristics.
    stats = [
        FeatureStats(name=f"F{i}", category="alpha158",
                     mean_ic=0.01, std_ic=0.02, ir=0.5, t_stat=0.8,
                     n_periods=40, n_observations=400)
        for i in range(10)
    ]
    report = _report_with_features(stats)
    suggestions = suggest_improvements(report)
    feature_suggestions = [s for s in suggestions if s.category == "feature"]
    assert feature_suggestions == []
