"""Trend Rider: pullback within an established uptrend.

Conceptual setup we want to surface:

    * Long-term trend is up   →  price above a rising SMA200
    * Medium-term trend is up →  SMA50 above SMA200 and itself rising
    * Right now we're pulling back, not extending: price has come down
      toward the rising SMA50 (the "trendline" proxy), and RSI(14) has
      cooled into the 30-45 band
    * Pullback is shallow relative to volatility — we are not in a regime
      change, just a breather

A clean Trend Rider gives us an entry just as the pullback finishes near
support, with a clear invalidation level (close below SMA50 by more than
~1 ATR) and an obvious target (prior swing high).

Score is built from four components with explicit weights so the narrative
layer can later say "RSI 36 contributed 18/20 points" without guessing.
"""

from __future__ import annotations

import pandas as pd

from src.scanner.indicators import (
    DIST_SMA50, DIST_SMA200, RSI14, SMA50, SMA200,
    SMA50_SLOPE_20, SMA200_SLOPE_20,
    has_min_history, snapshot,
)

from .base import Factor, MatchResult, clamp, triangular

NAME = "trend_rider"

# Minimum history we need before SMA200 + slope is meaningful.
MIN_BARS = 220

# Component weights — must sum to 100.
W_TREND = 35      # uptrend regime quality
W_PULLBACK = 25   # how close we are to the SMA50 from above
W_RSI = 25        # RSI in the cooling band
W_SLOPE = 15      # SMA50 rising hard enough to call it a real trend


def detect_trend_rider(
    df: pd.DataFrame, symbol: str
) -> MatchResult | None:
    """Return a :class:`MatchResult` if ``df`` shows a Trend Rider setup.

    ``df`` must already have indicator columns from
    :func:`src.scanner.indicators.add_indicators`. Returns ``None`` if the
    hard gates fail (no uptrend regime, insufficient history, etc.).
    """
    if not has_min_history(df, MIN_BARS):
        return None

    last = df.iloc[-1]
    close = float(last["close"])
    sma50 = last[SMA50]
    sma200 = last[SMA200]
    rsi = last[RSI14]
    sma50_slope = last[SMA50_SLOPE_20]
    sma200_slope = last[SMA200_SLOPE_20]
    dist50 = last[DIST_SMA50]
    dist200 = last[DIST_SMA200]

    # Any missing indicator means we can't fairly evaluate.
    for v in (sma50, sma200, rsi, sma50_slope, sma200_slope, dist50, dist200):
        if pd.isna(v):
            return None

    # ---- Hard gates: must hold or this is not a Trend Rider at all. ----
    if not (sma50 > sma200):
        return None  # No bullish MA stack.
    if sma200_slope <= 0:
        return None  # SMA200 not rising — not an uptrend.
    if close < sma200:
        return None  # Below the long-term anchor.
    if dist50 > 0.10:
        return None  # Too far above SMA50 — extended, not a pullback.
    if dist50 < -0.05:
        return None  # Broken below SMA50 by more than 5% — not "near" support.

    # ---- Scored components, each normalized to [0, 1]. ----

    # Trend quality: price comfortably above SMA200 (5-25% sweet spot),
    # SMA50 above SMA200 by a healthy margin (>=2%).
    sma_stack_gap = float(sma50 / sma200 - 1.0)
    trend_quality = (
        triangular(float(dist200), lo=0.0, peak=0.12, hi=0.40)
        * clamp(sma_stack_gap / 0.05)
    )

    # Pullback quality: peaks when price is right at the SMA50 (±0%),
    # tapers off as we drift up to +10% or down to -5%.
    if dist50 >= 0:
        pullback_quality = triangular(float(dist50), lo=-0.01, peak=0.005, hi=0.06)
    else:
        pullback_quality = triangular(float(dist50), lo=-0.05, peak=0.0, hi=0.005)

    # RSI cooling band: ideal at 38, acceptable 30-45.
    rsi_quality = triangular(float(rsi), lo=28.0, peak=38.0, hi=46.0)

    # Slope quality: SMA50 rising at >=2% over 20 bars is "trending"; we
    # cap the reward at 8% so a parabolic blow-off doesn't score higher
    # than a steady uptrend.
    slope_quality = clamp((float(sma50_slope) - 0.0) / 0.08)

    factors = (
        Factor(
            "trend_regime", float(dist200),
            trend_quality * W_TREND, W_TREND,
            "price vs SMA200, SMA50 vs SMA200 spacing",
        ),
        Factor(
            "pullback_to_sma50", float(dist50),
            pullback_quality * W_PULLBACK, W_PULLBACK,
            "distance from rising SMA50 (closer = better)",
        ),
        Factor(
            "rsi_cooling", float(rsi),
            rsi_quality * W_RSI, W_RSI,
            "RSI(14) in the 30-45 pullback band",
        ),
        Factor(
            "sma50_slope", float(sma50_slope),
            slope_quality * W_SLOPE, W_SLOPE,
            "SMA50 rising over the last 20 bars",
        ),
    )
    score = sum(f.contribution for f in factors)

    # Final safety net: if any of the meaningful components scored zero
    # (e.g. RSI is technically in range but right at the boundary), don't
    # surface it — Trend Rider needs all four legs at least partially.
    if any(f.contribution == 0.0 for f in factors):
        return None

    return MatchResult(
        symbol=symbol,
        pattern=NAME,
        score=float(score),
        as_of=df.index[-1],
        price=close,
        source=str(df.attrs.get("source", "unknown")),
        indicators=snapshot(df),
        factors=factors,
    )
