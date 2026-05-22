"""Tests for forward-return measurement (screener-mode backtest).

Pure-logic tests — no network. We build synthetic OHLCV in memory and
patch the unified fetcher so we can verify exact returns, the truncated
path, and the one-fetch-per-symbol contract.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from src.backtest import screener as screener_mod
from src.backtest.screener import (
    DEFAULT_HORIZONS_DAYS,
    ForwardOutcome,
    measure_forward_returns,
)
from src.backtest.signals import BacktestSignal
from src.utils.time import get_current_utc_date


# ---------------------------------------------------------------------- #
# Fixtures                                                               #
# ---------------------------------------------------------------------- #


def _step_frame(n: int, jump_at: int, low_price: float, high_price: float) -> pd.DataFrame:
    """Flat at ``low_price`` for bars [0, jump_at), then flat at ``high_price``.

    Open == close on every bar, so entry price is unambiguous: a signal at
    position ``jump_at - 2`` enters next bar (``jump_at - 1``) still at
    ``low_price``, and any close from bar ``jump_at`` onward yields
    ``high_price / low_price - 1``.
    """
    dates = pd.date_range("2020-01-02", periods=n, freq="B")
    prices = [low_price if i < jump_at else high_price for i in range(n)]
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p + 0.01 for p in prices],
            "low": [p - 0.01 for p in prices],
            "close": prices,
            "volume": [1_000_000] * n,
        },
        index=dates,
    )


def _signal(symbol: str, ts: pd.Timestamp, *, pattern: str = "trend_rider") -> BacktestSignal:
    return BacktestSignal(
        symbol=symbol,
        date=ts,
        pattern=pattern,
        score=72.0,
        price=100.0,
        sectors=("AI",),
    )


# ---------------------------------------------------------------------- #
# (a) Verifiable forward return on synthetic OHLCV                       #
# ---------------------------------------------------------------------- #


def test_forward_return_matches_synthetic_rally(monkeypatch):
    # 320 bars: flat at 100 through bar 51, then flat at 130 from bar 52.
    # Signal at bar 50 → entry at bar 51 open = 100 → forward returns from
    # bar 113 (= 50 + 63) onward see close = 130 → 30% return.
    frame = _step_frame(n=320, jump_at=52, low_price=100.0, high_price=130.0)
    signal_ts = frame.index[50]

    monkeypatch.setattr(
        screener_mod, "fetch_ohlcv",
        lambda symbol, lookback_days=400, **kwargs: frame,
    )

    outcomes = measure_forward_returns([_signal("TEST", signal_ts)])

    assert len(outcomes) == 1
    o = outcomes[0]
    assert isinstance(o, ForwardOutcome)
    assert o.horizons_days == DEFAULT_HORIZONS_DAYS
    assert o.forward_returns[63] == pytest.approx(0.30)
    # 21d and 126d and 252d are all in-window (320 > 50 + 252) and land on
    # the high plateau, so they should also be ~0.30.
    for h in DEFAULT_HORIZONS_DAYS:
        assert o.forward_returns[h] == pytest.approx(0.30)
    assert o.truncated is False
    assert o.max_favorable_excursion == pytest.approx(0.30)
    # The window starts at the entry bar (51), which is still on the 100
    # plateau — return there is exactly 0. From bar 52 onward it sits at
    # 130. So the trough of the window is 0.0, the peak is 0.30.
    assert o.max_adverse_excursion == pytest.approx(0.0)
    # First peak occurs at bar 52 (entry+1 → trading-day offset 2 from signal).
    assert o.days_to_peak == 2


# ---------------------------------------------------------------------- #
# (b) Signal too close to end-of-data → truncated                        #
# ---------------------------------------------------------------------- #


def test_signal_near_end_of_data_is_truncated(monkeypatch):
    # 81 bars total; signal at bar 50 leaves only 30 forward bars — enough
    # for the 21d horizon (50 + 21 = 71 < 81) but not 63d (50 + 63 = 113).
    frame = _step_frame(n=81, jump_at=51, low_price=100.0, high_price=110.0)
    signal_ts = frame.index[50]

    monkeypatch.setattr(
        screener_mod, "fetch_ohlcv",
        lambda symbol, lookback_days=400, **kwargs: frame,
    )

    outcomes = measure_forward_returns([_signal("TEST", signal_ts)])

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.truncated is True
    assert o.forward_returns[21] is not None
    assert o.forward_returns[63] is None
    assert o.forward_returns[126] is None
    assert o.forward_returns[252] is None


# ---------------------------------------------------------------------- #
# (c) Multi-symbol batch fetches each symbol exactly once                #
# ---------------------------------------------------------------------- #


def test_batch_fetches_each_symbol_once(monkeypatch):
    frame_a = _step_frame(n=320, jump_at=52, low_price=100.0, high_price=120.0)
    frame_b = _step_frame(n=320, jump_at=52, low_price=50.0, high_price=80.0)
    frames = {"AAA": frame_a, "BBB": frame_b}
    fetch_calls: list[str] = []

    def _stub_fetch(symbol: str, lookback_days: int = 400, **kwargs):
        fetch_calls.append(symbol)
        return frames[symbol]

    monkeypatch.setattr(screener_mod, "fetch_ohlcv", _stub_fetch)

    signals = [
        _signal("AAA", frame_a.index[50], pattern="trend_rider"),
        _signal("AAA", frame_a.index[60], pattern="bottom_hunter"),
        _signal("BBB", frame_b.index[50], pattern="trend_rider"),
        _signal("BBB", frame_b.index[70], pattern="trend_rider"),
    ]

    outcomes = measure_forward_returns(signals)

    assert sorted(fetch_calls) == ["AAA", "BBB"]
    assert len(fetch_calls) == 2
    assert len(outcomes) == 4
    # Each symbol resolved with its own price scale.
    aaa_first = next(o for o in outcomes if o.signal.symbol == "AAA" and o.signal.date == frame_a.index[50])
    bbb_first = next(o for o in outcomes if o.signal.symbol == "BBB" and o.signal.date == frame_b.index[50])
    assert aaa_first.forward_returns[63] == pytest.approx(0.20)   # 120/100 - 1
    assert bbb_first.forward_returns[63] == pytest.approx(0.60)   # 80/50 - 1


# ---------------------------------------------------------------------- #
# Regression: lookback must be anchored to "today" when end is None      #
# ---------------------------------------------------------------------- #


def test_lookback_anchored_to_today_when_no_end(monkeypatch):
    """Regression for the lookback bug surfaced during NVDA hand-verification.

    Sizing lookback off the signal *span* instead of off "today" caused the
    fetcher's cache coverage check to reject the cached frame for any
    signal older than a few hundred days — the signal date then fell
    outside the loaded index and every outcome came back truncated.

    The fetcher's cache coverage check requires required_start
    (= anchor - lookback*0.7 days) to be at or before the cached frame's
    start. To survive that AND still have ~12 months of forward bars
    available past the signal, the lookback we ask for must comfortably
    cover (today - earliest_signal) + the longest forward horizon.
    """
    today = get_current_utc_date()
    signal_date = pd.Timestamp(today - timedelta(days=540))

    captured: dict = {}

    def _stub(symbol: str, lookback_days: int = 400, **kwargs):
        captured["lookback_days"] = lookback_days
        captured["end_date"] = kwargs.get("end_date")
        return pd.DataFrame(
            {"open": [], "high": [], "low": [], "close": [], "volume": []},
            index=pd.DatetimeIndex([], name="date"),
        )

    monkeypatch.setattr(screener_mod, "fetch_ohlcv", _stub)

    sig = BacktestSignal(
        symbol="ZZZ",
        date=signal_date,
        pattern="trend_rider",
        score=70.0,
        price=0.0,
    )
    measure_forward_returns([sig])  # default horizons → max horizon = 252; end omitted

    days_back_to_signal = (today - signal_date.date()).days
    longest_horizon_trading_days = max(DEFAULT_HORIZONS_DAYS)   # 252
    buffer_days = 60   # weekends/holidays slack in the forward window

    expected_min = days_back_to_signal + longest_horizon_trading_days + buffer_days
    assert captured["lookback_days"] >= expected_min, (
        f"lookback_days={captured['lookback_days']} is too small: "
        f"need >= {expected_min} "
        f"(signal age {days_back_to_signal}d "
        f"+ {longest_horizon_trading_days}d horizon "
        f"+ {buffer_days}d buffer)"
    )
    # When end is None the fetcher should be left to use its own "today"
    # anchor — we only size lookback ourselves, we don't override end_date.
    assert captured["end_date"] is None
