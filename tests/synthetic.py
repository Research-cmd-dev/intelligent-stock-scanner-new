"""Synthetic OHLCV generators used by the scanner tests.

Each builder returns a DataFrame with the canonical columns expected by
``add_indicators`` — no network, fully deterministic given a seed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _ohlcv_from_close(
    close: np.ndarray, start: str = "2024-01-01", base_volume: int = 1_000_000
) -> pd.DataFrame:
    """Wrap a close series into a plausible OHLCV frame with tz-naive index."""
    n = len(close)
    idx = pd.date_range(start=start, periods=n, freq="B")
    # Cheap synthetic high/low/open around the close path.
    rng = np.random.RandomState(7)
    noise = rng.uniform(0.001, 0.01, size=n) * close
    high = close + noise
    low = close - noise
    open_ = np.concatenate(([close[0]], close[:-1]))  # prior close
    volume = (base_volume + rng.randint(-100_000, 100_000, size=n)).astype("int64")
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    df.index.name = "date"
    df.attrs["source"] = "synthetic"
    return df


def trend_rider_series(n: int = 260, seed: int = 1) -> pd.DataFrame:
    """A clean uptrend that pulls back to the rising SMA50 at the end.

    Construction: linear uptrend + light noise, then the last ~12 bars
    fade about 6% to bring price right back to SMA50 with RSI in the
    high-30s.
    """
    rng = np.random.RandomState(seed)
    trend = np.linspace(0.0, 0.80, n)          # +80% drift over the window
    noise = np.cumsum(rng.normal(0, 0.004, n))  # mild walk on top
    close = 100.0 * np.exp(trend + noise)

    # Engineer a controlled pullback in the last 14 bars — gentle enough
    # to keep RSI in the 32-42 band, deep enough to bring close back to
    # the rising SMA50.
    pullback_len = 14
    fade = np.linspace(0.0, -0.04, pullback_len)
    close[-pullback_len:] = close[-pullback_len - 1] * np.exp(fade)
    return _ohlcv_from_close(close)


def bottom_hunter_series(n: int = 260, seed: int = 2) -> pd.DataFrame:
    """A washout, mid-window low, and partial recovery.

    Construction is engineered so the 120-bar evaluation window contains
    the rounded bottom in its middle third:

        * Phase 1 (bars 0-60):    flat at the prior high
        * Phase 2 (bars 60-180):  -50% decline ( ~5 months )
        * Phase 3 (bars 180-220): basing U-shape, nadir near bar 200
        * Phase 4 (bars 220-260): +20% recovery off the lows

    The recovery is intentionally modest so the final RSI lands in the
    50-65 "recovering" band rather than overbought.
    """
    rng = np.random.RandomState(seed)

    flat_len = 60
    decline_len = 120
    base_len = 40
    cool_len = 10  # gentle pause at the end so RSI lands in 50-65, not >70
    recovery_len = n - flat_len - decline_len - base_len - cool_len

    flat = np.zeros(flat_len)
    decline = np.linspace(0.0, -0.65, decline_len)
    # U-shape: dip a bit further then lift back; nadir mid-phase.
    half = base_len // 2
    base = np.concatenate([
        np.linspace(-0.65, -0.72, half),
        np.linspace(-0.72, -0.62, base_len - half),
    ])
    recovery = np.linspace(-0.62, -0.48, recovery_len)
    cool = np.linspace(-0.48, -0.50, cool_len)
    drift = np.concatenate([flat, decline, base, recovery, cool])

    # Very mild noise so the engineered shape dominates RSI behavior.
    noise = np.cumsum(rng.normal(0, 0.0015, n))
    close = 100.0 * np.exp(drift + noise)
    return _ohlcv_from_close(close)


def bottom_hunter_already_rising_series(n: int = 260) -> pd.DataFrame:
    """Mild uptrend that has been rising for >40 bars and is now steeper.

    Constructed so that SMA50_SLOPE_20 ~40 bars ago is already positive.
    The Bottom Hunter curl gate (which now requires older_slope < 0) must
    return None — this is *not* a reversal from a prior downtrend.
    """
    # Gentle linear-ish updrift for the whole window, then a bit steeper at end.
    t = np.arange(n)
    # Phase: slow rise early, faster late — SMA50 slope positive throughout the
    # last ~60 bars.
    drift = 0.0008 * t + 0.0004 * np.maximum(0, t - (n - 60))
    noise = np.cumsum(np.random.RandomState(11).normal(0, 0.0008, n))
    close = 100.0 * np.exp(drift + noise)
    return _ohlcv_from_close(close)


def flat_chop_series(n: int = 260, seed: int = 3) -> pd.DataFrame:
    """Random walk around a flat mean — should NOT match either pattern."""
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
    return _ohlcv_from_close(close)
