"""Bottom Hunter: rounding bottom / cup-style recovery setup.

We are looking for stocks that have been in a real downtrend and are now
showing the first credible signs of a reversal — not catching a falling
knife, but stepping in once the floor is forming. The setup has four legs:

    1. **Prior damage.** The chart must have been beaten up: substantial
       drawdown from a 120-bar high, and a long stretch spent below the
       SMA200. No "shallow dip" recoveries.

    2. **Rounding base.** The 120-bar low sits in the middle of the window
       (not at the very right edge). Price has worked sideways or higher
       since that low — classic cup floor.

    3. **MA curl.** SMA50 was sloping *down* (negative) a couple months ago
       and is now sloping up. Continuation of an already-positive slope
       does not count — we want a genuine reversal inflection.

    4. **RSI recovery.** RSI(14) was oversold (<35) somewhere in the
       recent past and has lifted into the 45-65 zone — momentum coming
       back online without being overbought.

Bonus: price reclaiming the SMA50 from below adds to the score.
"""

from __future__ import annotations

import pandas as pd

from src.scanner.indicators import (
    DIST_SMA50, DD_120, RSI14, SMA50, SMA200,
    SMA50_SLOPE_20, has_min_history, snapshot,
)

from .base import Factor, MatchResult, clamp, triangular

NAME = "bottom_hunter"

# Need enough history to evaluate the 120-bar window and SMA200 context.
MIN_BARS = 220

# Lookback for the "base" window we analyze. 120 trading days ≈ 6 months.
BASE_WINDOW = 120

# Component weights — must sum to 100.
W_DAMAGE = 25     # was it actually beaten up
W_BASE = 25       # rounding shape — low in the middle, not the right edge
W_CURL = 25       # SMA50 has turned from down to up
W_RSI = 15        # RSI recovered from oversold
W_RECLAIM = 10    # price now above SMA50


def detect_bottom_hunter(
    df: pd.DataFrame, symbol: str
) -> MatchResult | None:
    """Return a :class:`MatchResult` if ``df`` shows a Bottom Hunter setup."""
    if not has_min_history(df, MIN_BARS):
        return None

    last = df.iloc[-1]
    close = float(last["close"])
    sma50 = last[SMA50]
    sma200 = last[SMA200]
    rsi = last[RSI14]
    sma50_slope = last[SMA50_SLOPE_20]
    dd120 = last[DD_120]
    dist50 = last[DIST_SMA50]

    for v in (sma50, sma200, rsi, sma50_slope, dd120, dist50):
        if pd.isna(v):
            return None

    window = df.tail(BASE_WINDOW)
    closes = window["close"]

    # ---- Hard gates ----
    # Must have had a real drawdown at some point in the window.
    window_dd_floor = float(closes.min() / closes.iloc[:30].max() - 1.0)
    if window_dd_floor > -0.15:
        return None  # Never dropped enough to count as a "bottom".

    # Must have spent meaningful time below SMA200 (i.e. in a downtrend).
    sma200_window = window[SMA200]
    below_sma200_frac = float((closes < sma200_window).mean())
    if below_sma200_frac < 0.30:
        return None

    # RSI must have actually been oversold somewhere in the recent past —
    # otherwise this is just a sideways chop, not a true washout.
    rsi_window = window[RSI14]
    rsi_min = float(rsi_window.min())
    if rsi_min > 35.0:
        return None

    # Don't flag something that's already ripped — current RSI must still
    # be in the "recovering" zone, not overbought.
    if rsi > 70.0:
        return None

    # ---- Scored components ----

    # Damage: bigger drawdown + more time below SMA200 = stronger setup.
    damage_quality = clamp(
        (clamp(-float(dd120) / 0.40) * 0.6)
        + (clamp((below_sma200_frac - 0.30) / 0.50) * 0.4)
    )

    # Rounding base: the 120-bar low should sit in the middle third of
    # the window — that's what makes it a "rounded" bottom rather than a
    # V or a still-falling chart. Compute where the min is positionally.
    low_idx = int(closes.values.argmin())
    base_pos = low_idx / max(len(closes) - 1, 1)  # 0 = oldest, 1 = newest
    # Ideal at 0.5, fades to 0 at 0.15 (too old, no recent test) or
    # 0.85 (too recent, still falling).
    base_quality = triangular(base_pos, lo=0.15, peak=0.50, hi=0.85)
    # Bonus: current price should be off the lows by a healthy margin
    # but not so far that we've missed the move (5-30% off the low).
    off_low = float(close / closes.min() - 1.0)
    base_quality *= clamp(off_low / 0.05) * (1 - clamp((off_low - 0.30) / 0.30))
    base_quality = clamp(base_quality)

    # MA curl: SMA50 should be flat-to-up now (positive slope), having
    # been *negative* ~40 bars ago (true reversal, not continuation of an
    # already-rising trend). We use the difference of slopes as the signal.
    older_slope = float(window[SMA50_SLOPE_20].iloc[40]) if len(window) > 40 else float("nan")
    if pd.isna(older_slope):
        curl_delta = 0.0
    else:
        curl_delta = float(sma50_slope) - older_slope
    # Hard gate: current slope up + meaningful curl from a *prior negative* slope.
    if float(sma50_slope) <= 0 or curl_delta <= 0 or (not pd.isna(older_slope) and older_slope >= 0):
        curl_quality = 0.0
    else:
        curl_quality = clamp(float(sma50_slope) / 0.05) * clamp(curl_delta / 0.05)

    # RSI recovery: ideal current RSI at 55, with credit only if min RSI
    # in window was genuinely oversold.
    rsi_quality = triangular(float(rsi), lo=40.0, peak=55.0, hi=68.0)
    rsi_quality *= clamp((35.0 - rsi_min) / 10.0 + 0.5)  # deeper washout = bigger bonus
    rsi_quality = clamp(rsi_quality)

    # Reclaim: price above SMA50 = bonus; below SMA50 = partial credit
    # if at least within 3% (still building the right side of the cup).
    if dist50 >= 0:
        reclaim_quality = clamp(1.0 - float(dist50) / 0.10)  # peaks at "just above"
    else:
        reclaim_quality = clamp(1.0 + float(dist50) / 0.03) * 0.5  # half credit at most

    factors = (
        Factor(
            "prior_damage", float(dd120),
            damage_quality * W_DAMAGE, W_DAMAGE,
            "drawdown from 120-bar high + time spent below SMA200",
        ),
        Factor(
            "rounding_base", base_pos,
            base_quality * W_BASE, W_BASE,
            "low sits mid-window and price is off the bottom",
        ),
        Factor(
            "sma50_curl", float(sma50_slope),
            curl_quality * W_CURL, W_CURL,
            "SMA50 has flipped from down-sloping to up-sloping",
        ),
        Factor(
            "rsi_recovery", float(rsi),
            rsi_quality * W_RSI, W_RSI,
            "RSI(14) was oversold and has recovered into 45-65",
        ),
        Factor(
            "reclaim_sma50", float(dist50),
            reclaim_quality * W_RECLAIM, W_RECLAIM,
            "price reclaiming SMA50 from below",
        ),
    )
    score = sum(f.contribution for f in factors)

    # Require at least the first three legs (damage, base, curl) to have
    # non-zero contributions — those are the structural ones. Without
    # them this isn't a Bottom Hunter even if RSI looks pretty.
    structural = (factors[0].contribution, factors[1].contribution, factors[2].contribution)
    if any(c == 0.0 for c in structural):
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
