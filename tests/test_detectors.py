"""End-to-end detector tests against engineered synthetic series."""

from __future__ import annotations

from src.scanner import (
    BOTTOM_HUNTER, TREND_RIDER,
    add_indicators, detect_bottom_hunter, detect_trend_rider,
)
from tests.synthetic import (
    bottom_hunter_already_rising_series,
    bottom_hunter_series,
    flat_chop_series,
    trend_rider_series,
)


def test_trend_rider_fires_on_engineered_uptrend_pullback() -> None:
    df = add_indicators(trend_rider_series())
    match = detect_trend_rider(df, "FAKE")
    assert match is not None
    assert match.pattern == TREND_RIDER
    assert match.score > 40
    # Sanity: RSI in the cooling band, price near SMA50.
    assert 28 <= match.indicators["rsi14"] <= 50
    assert abs(match.indicators["dist_sma50"]) < 0.10


def test_trend_rider_silent_on_downtrend() -> None:
    df = add_indicators(bottom_hunter_series())
    assert detect_trend_rider(df, "FAKE") is None


def test_trend_rider_silent_on_flat_chop() -> None:
    df = add_indicators(flat_chop_series())
    assert detect_trend_rider(df, "FAKE") is None


def test_bottom_hunter_fires_on_engineered_recovery() -> None:
    df = add_indicators(bottom_hunter_series())
    match = detect_bottom_hunter(df, "FAKE")
    assert match is not None
    assert match.pattern == BOTTOM_HUNTER
    assert match.score > 30
    # Structural factors must all have contributed.
    structural = {f.name for f in match.factors[:3]}
    assert structural == {"prior_damage", "rounding_base", "sma50_curl"}
    for f in match.factors[:3]:
        assert f.contribution > 0


def test_bottom_hunter_silent_on_uptrend() -> None:
    df = add_indicators(trend_rider_series())
    assert detect_bottom_hunter(df, "FAKE") is None


def test_bottom_hunter_silent_on_flat_chop() -> None:
    df = add_indicators(flat_chop_series())
    assert detect_bottom_hunter(df, "FAKE") is None


def test_bottom_hunter_silent_on_already_rising_trend() -> None:
    """Chart already in mild uptrend 40+ bars ago (older_slope >= 0) must not fire.

    The tightened curl gate (older_slope < 0 required) prevents false positives
    on continuation rather than reversal.
    """
    df = add_indicators(bottom_hunter_already_rising_series())
    assert detect_bottom_hunter(df, "FAKE") is None


def test_match_to_row_is_dashboard_ready() -> None:
    df = add_indicators(trend_rider_series())
    match = detect_trend_rider(df, "FAKE")
    assert match is not None
    row = match.to_row()
    for key in ("symbol", "pattern", "score", "as_of", "price", "rsi14"):
        assert key in row
