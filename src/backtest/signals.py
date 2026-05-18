"""Walk-forward signal generation.

Replays the scanner over historical OHLCV bar-by-bar. For each symbol
we fetch the full series once (cache-friendly), enrich it with
indicators once, and then slice the enriched frame as we step forward.
This keeps the historical replay cheap — no re-fetches, no indicator
recomputation — while exercising the *exact* detector code paths the
live dashboard uses.

Signals are emitted as :class:`BacktestSignal` records, deliberately
denormalised so the trade engine and the refinement layer can both
consume them without reaching back into the scanner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

import pandas as pd

from src.data import fetch_ohlcv
from src.scanner import Scanner
from src.scanner.indicators import add_indicators, has_min_history
from src.utils import get_logger

log = get_logger(__name__)


# How many calendar days of history we request per symbol before the
# backtest window starts. SMA200 needs ~200 trading days; we ask for
# ~1.5x that to leave room for non-trading days and the indicator warmup.
WARMUP_DAYS = 400


@dataclass(frozen=True)
class BacktestSignal:
    """One pattern hit emitted during historical replay.

    ``narrative_score`` is optional — backtests typically run pattern-only
    because historical news availability is uneven (yfinance has no
    backfill, Polygon's per-article sentiment depends on the plan). The
    field is here so a future, news-aware backtest can populate it.
    """

    symbol: str
    date: pd.Timestamp
    pattern: str
    score: float
    price: float
    sectors: tuple[str, ...] = ()
    narrative_score: float | None = None
    factors: dict[str, float] = field(default_factory=dict)


def generate_signals(
    symbols: Iterable[str],
    *,
    start: str | date | pd.Timestamp,
    end: str | date | pd.Timestamp,
    min_score: float = 50.0,
    scanner: Scanner | None = None,
    warmup_days: int = WARMUP_DAYS,
) -> list[BacktestSignal]:
    """Replay the scanner across ``symbols`` between ``start`` and ``end``.

    Args:
        symbols: Tickers to scan.
        start: First in-window bar (inclusive). Earlier bars feed the
            indicator warmup but never produce signals.
        end: Last in-window bar (inclusive).
        min_score: Pattern score floor. Same semantics as ``Scanner.min_score``.
        scanner: Optional pre-built :class:`Scanner`. Pass one to override
            the detector set; otherwise the default registry is used. The
            scanner's ``min_score`` is overridden by the ``min_score`` arg
            here so the two stay in sync.
        warmup_days: Lookback fetched before ``start`` so indicators are
            valid on the first in-window bar.

    Returns:
        Signals sorted by ``(date, symbol, pattern)`` ascending.
    """
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if end_ts < start_ts:
        raise ValueError("end must be on or after start")

    scanner = scanner or Scanner(min_score=min_score)
    out: list[BacktestSignal] = []

    fetch_lookback = (
        (end_ts - start_ts).days + warmup_days + 30
    )  # +30 day slop for weekends/holidays

    for symbol in sorted({s.upper() for s in symbols}):
        try:
            raw = fetch_ohlcv(symbol, lookback_days=fetch_lookback)
        except Exception as exc:
            log.warning("fetch failed for %s: %s", symbol, exc)
            continue
        if not has_min_history(raw, 220):
            log.info("skipping %s: insufficient history", symbol)
            continue

        enriched = add_indicators(raw)
        out.extend(
            _replay_symbol(enriched, symbol, start_ts, end_ts, scanner, min_score)
        )

    out.sort(key=lambda s: (s.date, s.symbol, s.pattern))
    log.info("generated %d signals across %d symbols", len(out), len(set(s.symbol for s in out)))
    return out


def _replay_symbol(
    enriched: pd.DataFrame,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    scanner: Scanner,
    min_score: float,
) -> list[BacktestSignal]:
    """Step through one symbol's enriched frame, emitting signals per bar."""
    idx = enriched.index
    # Index positions of bars inside the [start, end] window.
    mask = (idx >= start) & (idx <= end)
    target_positions = [i for i, m in enumerate(mask) if m]
    if not target_positions:
        return []

    out: list[BacktestSignal] = []
    for pos in target_positions:
        # Slice up-to-and-including the current bar. The scanner sees only
        # data the live dashboard would have on that day — no look-ahead.
        slice_ = enriched.iloc[: pos + 1]
        try:
            matches = scanner.scan_frame(slice_, symbol)
        except Exception as exc:
            log.warning("scan_frame failed for %s @ %s: %s", symbol, idx[pos], exc)
            continue

        for m in matches:
            if m.score < min_score:
                continue
            out.append(
                BacktestSignal(
                    symbol=symbol,
                    date=idx[pos],
                    pattern=m.pattern,
                    score=float(m.score),
                    price=float(m.price),
                    sectors=tuple(m.sectors),
                    factors={f.name: f.contribution for f in m.factors},
                )
            )
    return out
