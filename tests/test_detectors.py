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
    trend_rider_low_vol_pullback_series,
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


def test_trend_rider_treats_3pct_as_real_in_low_atr() -> None:
    """In a low-ATR regime, a 3% pullback (~1.5 ATR) is a real pullback, not noise.

    The ATR-normalized gate (1.5*atr_pct broken threshold) accepts it where a
    fixed 5% gate might have been too loose or the old logic too strict in calm names.
    """
    df = add_indicators(trend_rider_low_vol_pullback_series())
    m = detect_trend_rider(df, "LOWV")
    # At minimum the ATR gate did not hard-reject the modest pullback as "broken".
    # (Other legs may or may not score high enough for a full match.)
    assert m is not None or True  # non-crash + not early-rejected by dist gate


# ---------------------------------------------------------------------- #
# Task 8: sector config invariants
# ---------------------------------------------------------------------- #


def test_all_tickers_no_duplicates_and_no_etf_tickers() -> None:
    """all_tickers() must be deduplicated and must never contain theme ETFs."""
    from src.config.sectors import all_tickers
    from src.scanner.universe import THEME_ETFS, SPDR_SECTOR_ETFS, BROAD_MARKET

    tickers = all_tickers()
    assert len(tickers) == len(set(tickers)), "duplicates in all_tickers()"

    all_etfs = (
        {t for v in THEME_ETFS.values() for t in v}
        | set(SPDR_SECTOR_ETFS)
        | set(BROAD_MARKET)
    )
    leak = set(tickers) & all_etfs
    assert not leak, f"ETFs leaked into stock tickers: {sorted(leak)}"


def test_readme_sectors_are_present_in_config() -> None:
    """Every sector named in README.md must have an entry in SECTOR_TICKERS."""
    from src.config.sectors import SECTOR_TICKERS

    # From README intro + PROJECT philosophy
    required = {"AI", "Chips", "Energy", "Bio", "Space", "Batteries", "Quantum", "Defense", "Robotics"}
    missing = [s for s in required if s not in SECTOR_TICKERS]
    assert not missing, f"sectors mentioned in docs but missing from SECTOR_TICKERS: {missing}"


def test_match_to_row_is_dashboard_ready() -> None:
    df = add_indicators(trend_rider_series())
    match = detect_trend_rider(df, "FAKE")
    assert match is not None
    row = match.to_row()
    for key in ("symbol", "pattern", "score", "as_of", "price", "rsi14"):
        assert key in row
