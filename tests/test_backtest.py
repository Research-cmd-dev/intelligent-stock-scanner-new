"""Tests for the backtest layer.

These are pure-logic tests — no network. We build synthetic
:class:`BacktestSignal` objects and price frames in memory, then drive
the trade simulator, metrics, and refinement heuristics directly. This
mirrors the test style of the scanner layer and keeps CI fast.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.backtest.engine import (
    BacktestReport,
    Trade,
    simulate_trades,
)
from src.backtest.metrics import (
    breakdown_by,
    breakdown_by_score_band,
    compute_metrics,
    daily_returns_from_trades,
)
from src.backtest.refine import (
    MIN_BUCKET_TRADES,
    MIN_TOTAL_TRADES_FOR_CONFIDENCE,
    suggest_improvements,
)
from src.backtest.signals import BacktestSignal


# ---------------------------------------------------------------------- #
# Fixtures                                                               #
# ---------------------------------------------------------------------- #


def _ramp_frame(n: int = 60, start_price: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """OHLCV with a linearly rising close — every trade should be profitable."""
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    closes = [start_price + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
        },
        index=dates,
    )


def _signal(
    symbol: str = "TEST",
    date: str = "2024-01-05",
    pattern: str = "trend_rider",
    score: float = 72.0,
    price: float = 100.0,
    sectors: tuple[str, ...] = ("AI",),
) -> BacktestSignal:
    return BacktestSignal(
        symbol=symbol,
        date=pd.Timestamp(date),
        pattern=pattern,
        score=score,
        price=price,
        sectors=sectors,
    )


def _trade(
    return_pct: float = 0.05,
    *,
    pattern: str = "trend_rider",
    score: float = 72.0,
    sectors: tuple[str, ...] = ("AI",),
    hold_days: int = 20,
) -> Trade:
    """Forge a Trade directly with a target return — bypasses price math."""
    sig = _signal(pattern=pattern, score=score, sectors=sectors)
    entry = 100.0
    exit_ = entry * (1.0 + return_pct)
    return Trade(
        signal=sig,
        entry_date=pd.Timestamp("2024-01-06"),
        entry_price=entry,
        exit_date=pd.Timestamp("2024-02-06"),
        exit_price=exit_,
        hold_days=hold_days,
    )


# ---------------------------------------------------------------------- #
# simulate_trades                                                        #
# ---------------------------------------------------------------------- #


def test_simulate_trades_enters_next_open_and_exits_after_hold_days():
    frame = _ramp_frame()  # close rises by $1/day; opens equal closes
    sig = _signal(date="2024-01-05", price=float(frame.loc["2024-01-05", "close"]))

    trades = simulate_trades([sig], {"TEST": frame}, hold_days=10)

    assert len(trades) == 1
    t = trades[0]
    # Entry is the *next* business day after 2024-01-05 (Fri) — 2024-01-08.
    assert t.entry_date == pd.Timestamp("2024-01-08")
    assert t.hold_days == 10
    assert t.return_pct > 0  # rising ramp
    assert not t.truncated


def test_simulate_trades_truncates_when_window_runs_out():
    frame = _ramp_frame(n=10)
    # Signal on bar index 7 with hold_days=20 — only 2 bars remain.
    sig_date = frame.index[7]
    sig = _signal(date=str(sig_date.date()), price=float(frame.loc[sig_date, "close"]))

    trades = simulate_trades([sig], {"TEST": frame}, hold_days=20)
    assert len(trades) == 1
    assert trades[0].truncated
    assert trades[0].hold_days == len(frame) - 1 - 8  # ran to last bar


def test_simulate_trades_skips_signal_on_last_bar():
    frame = _ramp_frame(n=5)
    last_date = frame.index[-1]
    sig = _signal(date=str(last_date.date()))
    trades = simulate_trades([sig], {"TEST": frame}, hold_days=5)
    assert trades == []


def test_simulate_trades_respects_cooldown():
    frame = _ramp_frame(n=60)
    # Two signals on the same (symbol, pattern), 5 bars apart.
    s1 = _signal(date="2024-01-05")
    s2 = _signal(date="2024-01-12")  # five business days later
    trades = simulate_trades(
        [s1, s2], {"TEST": frame}, hold_days=10, cooldown_days=60
    )
    # Cooldown puts next-eligible past the second signal date — only one trade.
    assert len(trades) == 1


# ---------------------------------------------------------------------- #
# metrics                                                                #
# ---------------------------------------------------------------------- #


def test_compute_metrics_zero_trades_returns_zero_filled():
    m = compute_metrics([])
    assert m.trade_count == 0
    assert m.win_rate == 0.0
    assert m.profit_factor == 0.0


def test_compute_metrics_mixed_winners_and_losers():
    trades = [_trade(0.10), _trade(-0.05), _trade(0.20), _trade(-0.03)]
    m = compute_metrics(trades)
    assert m.trade_count == 4
    assert m.win_rate == 0.5
    assert m.best_trade == pytest.approx(0.20)
    assert m.worst_trade == pytest.approx(-0.05)
    # gross profit 0.30, gross loss 0.08 → pf 3.75
    assert m.profit_factor == pytest.approx(0.30 / 0.08)


def test_compute_metrics_profit_factor_infinite_with_no_losers():
    trades = [_trade(0.05), _trade(0.10)]
    m = compute_metrics(trades)
    assert math.isinf(m.profit_factor)


def test_max_drawdown_tracks_peak_to_trough():
    trades = [_trade(0.10), _trade(0.20), _trade(-0.40), _trade(0.05)]
    m = compute_metrics(trades)
    # equity: 0.10 → 0.30 → -0.10 → -0.05; peak 0.30, trough -0.10 → DD -0.40
    assert m.max_drawdown == pytest.approx(-0.40)


def test_breakdown_by_pattern_partitions_trades():
    trades = [
        _trade(0.10, pattern="trend_rider"),
        _trade(0.05, pattern="trend_rider"),
        _trade(-0.05, pattern="bottom_hunter"),
    ]
    bp = breakdown_by(trades, "pattern")
    assert set(bp.keys()) == {"trend_rider", "bottom_hunter"}
    assert bp["trend_rider"].trade_count == 2
    assert bp["bottom_hunter"].trade_count == 1


def test_breakdown_by_sector_fans_multi_sector_signals():
    trades = [
        _trade(0.10, sectors=("AI", "Chips")),
        _trade(0.20, sectors=("AI",)),
    ]
    bs = breakdown_by(trades, "sector")
    assert bs["AI"].trade_count == 2
    assert bs["Chips"].trade_count == 1


def test_breakdown_by_score_band_uses_default_bands():
    trades = [
        _trade(0.05, score=55),
        _trade(0.10, score=65),
        _trade(0.20, score=85),
    ]
    bands = breakdown_by_score_band(trades)
    assert "50-59" in bands
    assert "60-69" in bands
    assert "80-100" in bands


def test_daily_returns_spreads_evenly_over_hold():
    t = _trade(0.10, hold_days=10)
    daily = daily_returns_from_trades([t])
    assert len(daily) == 10
    assert daily[0] == pytest.approx(0.01)


# ---------------------------------------------------------------------- #
# refinement heuristics                                                  #
# ---------------------------------------------------------------------- #


def _make_report(trades: list[Trade], **overrides) -> BacktestReport:
    """Assemble a BacktestReport directly from trades for refinement tests."""
    metrics = compute_metrics(trades)
    base = dict(
        signals=[t.signal for t in trades],
        trades=trades,
        metrics=metrics,
        by_pattern=breakdown_by(trades, "pattern"),
        by_sector=breakdown_by(trades, "sector"),
        by_score_band=breakdown_by_score_band(trades),
        params={"hold_days": 20},
    )
    base.update(overrides)
    return BacktestReport(**base)


def test_no_trades_produces_a_high_priority_coverage_suggestion():
    report = _make_report([])
    suggestions = suggest_improvements(report)
    assert any(s.category == "coverage" and s.priority == "high" for s in suggestions)


def test_thin_sample_blocks_other_heuristics():
    trades = [_trade(0.10) for _ in range(5)]  # below MIN_TOTAL_TRADES
    report = _make_report(trades)
    suggestions = suggest_improvements(report)
    categories = {s.category for s in suggestions}
    assert "coverage" in categories
    # No pattern / score / sector flags should fire on this tiny sample.
    assert categories <= {"coverage"}


def test_pattern_gap_check_fires_when_one_pattern_clearly_lags():
    # Trend Rider: all winners. Bottom Hunter: all losers. Way more than
    # the 15pp win-rate gap threshold.
    trades = (
        [_trade(0.10, pattern="trend_rider") for _ in range(MIN_BUCKET_TRADES)] +
        [_trade(-0.05, pattern="bottom_hunter") for _ in range(MIN_BUCKET_TRADES)]
    )
    # Pad to clear MIN_TOTAL_TRADES_FOR_CONFIDENCE.
    while len(trades) < MIN_TOTAL_TRADES_FOR_CONFIDENCE:
        trades.append(_trade(0.05, pattern="trend_rider"))
    report = _make_report(trades)
    suggestions = suggest_improvements(report)
    pattern = [s for s in suggestions if s.category == "pattern"]
    assert pattern, "expected a pattern suggestion"
    assert "bottom_hunter" in pattern[0].title


def test_score_band_check_recommends_higher_floor():
    low = [_trade(-0.05, score=55) for _ in range(MIN_BUCKET_TRADES)]
    high = [_trade(0.15, score=82) for _ in range(MIN_BUCKET_TRADES)]
    trades = low + high
    while len(trades) < MIN_TOTAL_TRADES_FOR_CONFIDENCE:
        trades.append(_trade(0.10, score=75))
    report = _make_report(trades)
    suggestions = suggest_improvements(report)
    score_suggestions = [s for s in suggestions if s.category == "score_threshold"]
    assert score_suggestions
    assert score_suggestions[0].evidence["suggested_min_score"] in (60, 70)


def test_negative_mean_return_triggers_high_priority_pattern_flag():
    trades = [_trade(-0.05) for _ in range(MIN_TOTAL_TRADES_FOR_CONFIDENCE)]
    report = _make_report(trades)
    suggestions = suggest_improvements(report)
    high_priority = [s for s in suggestions if s.priority == "high" and s.category == "pattern"]
    assert high_priority


def test_clean_run_emits_no_suggestions():
    # Steady winners, single pattern, single sector — no actionable signal.
    trades = [_trade(0.04) for _ in range(MIN_TOTAL_TRADES_FOR_CONFIDENCE * 2)]
    report = _make_report(trades)
    suggestions = suggest_improvements(report)
    assert suggestions == []
