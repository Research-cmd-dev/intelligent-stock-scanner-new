"""Sanity tests for the indicator layer."""

from __future__ import annotations

import math

import pytest

from src.scanner.indicators import (
    DD_120, DIST_SMA200, RSI14, SMA50, SMA200,
    add_indicators, has_min_history, snapshot,
)
from tests.synthetic import trend_rider_series


def test_add_indicators_populates_expected_columns() -> None:
    df = add_indicators(trend_rider_series())
    for col in (SMA50, SMA200, RSI14, DD_120, DIST_SMA200):
        assert col in df.columns
    last = df.iloc[-1]
    assert not math.isnan(last[SMA50])
    assert not math.isnan(last[SMA200])
    # RSI is bounded.
    assert 0 <= last[RSI14] <= 100


def test_snapshot_drops_nan_and_returns_floats() -> None:
    df = add_indicators(trend_rider_series())
    snap = snapshot(df)
    assert snap["close"] > 0
    for v in snap.values():
        assert isinstance(v, float)
        assert not math.isnan(v)


def test_has_min_history() -> None:
    df = add_indicators(trend_rider_series())
    assert has_min_history(df, 200)
    assert not has_min_history(df, 10_000)


def test_validate_rejects_missing_columns() -> None:
    df = trend_rider_series().drop(columns=["volume"])
    with pytest.raises(ValueError, match="missing columns"):
        add_indicators(df)
