"""Forward-return measurement for screener-mode backtests.

Replaces ``simulate_trades()`` for the screener evaluation framework.
Given a list of historical signals, we look up each symbol's OHLCV once
and compute forward returns at multiple horizons — no trades, no exits,
no PnL. The output is a distribution of "what did the stock do after we
flagged it" that downstream metrics can aggregate into hit rates,
right-tail summaries, and baseline-comparable excess returns.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from src.backtest.signals import BacktestSignal
from src.data import fetch_ohlcv
from src.utils import get_logger
from src.utils.time import get_current_utc_date

log = get_logger(__name__)


DEFAULT_HORIZONS_DAYS = (21, 63, 126, 252)   # ~1mo, 3mo, 6mo, 12mo (trading days)
DEFAULT_THRESHOLDS = (0.10, 0.20, 0.50, 1.00)

# Calendar-day buffer to cover the longest forward horizon (252 trading
# days ≈ 365 calendar days) plus weekends/holidays slack when we ask the
# fetcher how far back to load.
_FORWARD_BUFFER_DAYS = 400


@dataclass(frozen=True)
class ForwardOutcome:
    """A signal plus what the stock did afterwards. No trade, no exit, no PnL."""

    signal: BacktestSignal
    horizons_days: tuple[int, ...]
    forward_returns: dict[int, float | None]   # horizon -> return; None if truncated
    max_favorable_excursion: float | None      # peak return in longest horizon window
    max_adverse_excursion: float | None        # trough return in longest horizon window
    days_to_peak: int | None                   # trading days from signal to MFE peak
    truncated: bool                            # signal too close to end of available data

    def to_row(self) -> dict:
        """Flatten into a dict suitable for DataFrame / CSV export."""
        row: dict = {
            "symbol": self.signal.symbol,
            "date": self.signal.date,
            "pattern": self.signal.pattern,
            "score": self.signal.score,
            "price": self.signal.price,
            "sectors": ",".join(self.signal.sectors),
            "narrative_score": self.signal.narrative_score,
            "mfe": self.max_favorable_excursion,
            "mae": self.max_adverse_excursion,
            "days_to_peak": self.days_to_peak,
            "truncated": self.truncated,
        }
        for h in self.horizons_days:
            row[f"return_{h}d"] = self.forward_returns.get(h)
        return row


def measure_forward_returns(
    signals: list[BacktestSignal],
    *,
    horizons_days: tuple[int, ...] = DEFAULT_HORIZONS_DAYS,
    end: date | None = None,
) -> list[ForwardOutcome]:
    """For each signal, look up the symbol's forward price action and compute returns.

    One fetch per unique symbol — disk-cache friendly. Signals on the same
    symbol share a single OHLCV frame. Signals with no usable forward data
    (missing OHLCV, signal date not in index, no next bar for entry) return
    an all-None outcome with ``truncated=True`` rather than raising.

    Args:
        signals: Historical signals to evaluate.
        horizons_days: Forward horizons in trading days. Default
            ``(21, 63, 126, 252)`` ≈ 1mo / 3mo / 6mo / 12mo.
        end: Optional anchor for the fetcher's ``end_date``. Pass it when
            running an as-of historical backtest so the cache lookup is
            deterministic; leave None to use the live "today" anchor.

    Returns:
        One :class:`ForwardOutcome` per input signal, sorted by
        ``(date, symbol, pattern)`` ascending.
    """
    if not signals:
        return []

    by_symbol: dict[str, list[BacktestSignal]] = defaultdict(list)
    for sig in signals:
        by_symbol[sig.symbol].append(sig)

    max_horizon = max(horizons_days)
    outcomes: list[ForwardOutcome] = []

    for symbol, sym_signals in by_symbol.items():
        earliest = min(s.date for s in sym_signals).date()
        # The fetcher needs history going back to the earliest signal, plus
        # enough forward room past the latest signal to cover max_horizon.
        # When end is None we anchor to "today" (the fetcher's default
        # behavior) and size lookback so the cache coverage check accepts
        # the cached window — otherwise old signals fall outside the
        # cached frame and every outcome comes back truncated.
        anchor: date | None = end
        anchor_for_lookback = end if end is not None else get_current_utc_date()
        lookback_days = max(400, (anchor_for_lookback - earliest).days + _FORWARD_BUFFER_DAYS)

        try:
            df = fetch_ohlcv(symbol, lookback_days=lookback_days, end_date=anchor)
        except Exception as exc:  # noqa: BLE001 — never let one symbol kill the batch
            log.warning("fetch failed for %s: %s", symbol, exc)
            df = None

        for sig in sym_signals:
            outcomes.append(_one_outcome(sig, df, horizons_days, max_horizon))

    outcomes.sort(key=lambda o: (o.signal.date, o.signal.symbol, o.signal.pattern))
    return outcomes


def _one_outcome(
    signal: BacktestSignal,
    df: pd.DataFrame | None,
    horizons_days: tuple[int, ...],
    max_horizon: int,
) -> ForwardOutcome:
    empty = {h: None for h in horizons_days}
    truncated_outcome = ForwardOutcome(
        signal=signal,
        horizons_days=horizons_days,
        forward_returns=empty,
        max_favorable_excursion=None,
        max_adverse_excursion=None,
        days_to_peak=None,
        truncated=True,
    )

    if df is None or df.empty:
        return truncated_outcome

    idx = df.index
    try:
        signal_pos = int(idx.get_loc(signal.date))
    except KeyError:
        return truncated_outcome

    entry_pos = signal_pos + 1
    if entry_pos >= len(idx):
        return truncated_outcome

    entry_price = float(df.iloc[entry_pos]["open"])
    if not entry_price or pd.isna(entry_price):
        return truncated_outcome

    forward_returns: dict[int, float | None] = {}
    any_truncated = False
    for h in horizons_days:
        target_pos = signal_pos + h
        if target_pos >= len(idx):
            forward_returns[h] = None
            any_truncated = True
        else:
            close_at_h = float(df.iloc[target_pos]["close"])
            forward_returns[h] = close_at_h / entry_price - 1.0

    window_end = min(signal_pos + max_horizon, len(idx) - 1)
    closes = df.iloc[entry_pos : window_end + 1]["close"].astype(float)
    if closes.empty:
        return ForwardOutcome(
            signal=signal,
            horizons_days=horizons_days,
            forward_returns=forward_returns,
            max_favorable_excursion=None,
            max_adverse_excursion=None,
            days_to_peak=None,
            truncated=True,
        )
    window_returns = closes / entry_price - 1.0
    mfe = float(window_returns.max())
    mae = float(window_returns.min())
    # argmax gives the offset from entry_pos; trading-day offset from the
    # signal bar is +1 because entry sits one bar after the signal.
    peak_offset_from_entry = int(window_returns.values.argmax())
    days_to_peak = peak_offset_from_entry + 1

    return ForwardOutcome(
        signal=signal,
        horizons_days=horizons_days,
        forward_returns=forward_returns,
        max_favorable_excursion=mfe,
        max_adverse_excursion=mae,
        days_to_peak=days_to_peak,
        truncated=any_truncated,
    )
