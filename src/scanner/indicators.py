"""Technical indicators for the scanner.

Single entry point: :func:`add_indicators` takes a raw OHLCV DataFrame (as
produced by ``src.data.fetcher``) and returns a copy with a fixed set of
columns added. Detectors read those columns instead of recomputing.

Every indicator here is deterministic on the input close/volume series — no
forward-looking peeks, no NaN-fills that would mask insufficient history. If
a symbol has fewer bars than an indicator's lookback, that column is simply
NaN for the missing rows and detectors must guard accordingly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta

# Column names this module guarantees to add. Detectors reference these
# constants instead of string literals so a rename is a single-file change.
SMA20 = "sma20"
SMA50 = "sma50"
SMA200 = "sma200"
EMA21 = "ema21"
RSI14 = "rsi14"
ATR14 = "atr14"
VOL_SMA20 = "vol_sma20"
RET_20 = "ret_20"
RET_60 = "ret_60"
DD_120 = "drawdown_120"
SMA50_SLOPE_20 = "sma50_slope_20"
SMA200_SLOPE_20 = "sma200_slope_20"
DIST_SMA50 = "dist_sma50"
DIST_SMA200 = "dist_sma200"

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with technical indicator columns added.

    Input must contain the canonical OHLCV columns from the data fetcher.
    Output preserves the original index and columns, then appends the
    indicator columns declared at the top of this module.
    """
    _validate(df)
    out = df.copy()

    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    out[SMA20] = ta.sma(close, length=20)
    out[SMA50] = ta.sma(close, length=50)
    out[SMA200] = ta.sma(close, length=200)
    out[EMA21] = ta.ema(close, length=21)
    out[RSI14] = ta.rsi(close, length=14)
    out[ATR14] = ta.atr(high, low, close, length=14)
    out[VOL_SMA20] = ta.sma(volume, length=20)

    # Returns are pct moves over N bars; useful for momentum filters.
    out[RET_20] = close.pct_change(20)
    out[RET_60] = close.pct_change(60)

    # Drawdown from the trailing 120-bar high — a cheap "how deep is the
    # current pullback / how recent was the cycle peak" signal.
    roll_high_120 = close.rolling(120, min_periods=20).max()
    out[DD_120] = close / roll_high_120 - 1.0

    # SMA slopes via simple 20-bar % change of the moving average itself.
    # We use pct rather than a regression slope so the value is unit-free
    # and comparable across price levels.
    out[SMA50_SLOPE_20] = out[SMA50].pct_change(20)
    out[SMA200_SLOPE_20] = out[SMA200].pct_change(20)

    # Distance of close from each long-term MA, as a signed fraction.
    out[DIST_SMA50] = close / out[SMA50] - 1.0
    out[DIST_SMA200] = close / out[SMA200] - 1.0

    return out


def snapshot(df: pd.DataFrame) -> dict[str, float]:
    """Compact dict of the indicator values on the most recent bar.

    Convenience for detectors that want to embed a numeric snapshot in their
    :class:`MatchResult` without exposing the whole DataFrame. NaN-valued
    indicators are dropped so the snapshot only contains usable numbers.
    """
    if df.empty:
        return {}
    last = df.iloc[-1]
    fields = (
        "close", "volume",
        SMA20, SMA50, SMA200, EMA21,
        RSI14, ATR14, VOL_SMA20,
        RET_20, RET_60, DD_120,
        SMA50_SLOPE_20, SMA200_SLOPE_20,
        DIST_SMA50, DIST_SMA200,
    )
    out: dict[str, float] = {}
    for f in fields:
        if f not in last.index:
            continue
        val = last[f]
        if pd.isna(val):
            continue
        out[f] = float(val)
    return out


def _validate(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("OHLCV frame must have a DatetimeIndex")
    if len(df) < 2:
        raise ValueError("OHLCV frame needs at least 2 rows")


def has_min_history(df: pd.DataFrame, bars: int) -> bool:
    """True if the frame holds at least ``bars`` non-null close prices."""
    return df["close"].dropna().shape[0] >= bars


# Re-exported so callers don't have to know whether to import numpy.
nan = np.nan
