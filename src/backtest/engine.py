"""Trade simulation + report assembly.

Translates :class:`BacktestSignal` records into :class:`Trade` records by
buying at the next bar's open and selling ``hold_days`` later at close.
This is the simplest no-look-ahead execution model that still respects
real-trading mechanics (you can't buy at today's close after seeing
today's close).

A signal that doesn't have enough forward bars to complete its hold
window is held to the last available bar instead of being dropped, so
recent signals still contribute to the report — they just have shorter
``hold_days``.

The public entry point :func:`run_backtest` orchestrates everything:
signal generation → trade simulation → metrics → report. Each layer is
also importable on its own for tests and ad-hoc analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from src.data import fetch_ohlcv
from src.utils import get_logger

from .metrics import (
    Metrics,
    breakdown_by,
    breakdown_by_score_band,
    compute_metrics,
    compute_qlib_metrics,
    daily_returns_from_trades,
)
from .signals import WARMUP_DAYS, BacktestSignal, generate_signals

if TYPE_CHECKING:
    from src.features import FeatureEvaluation

log = get_logger(__name__)


# Default forward-holding window. 20 trading days ≈ one calendar month —
# long enough for Trend Rider continuations to play out, short enough to
# free capital for Bottom Hunter rotations.
DEFAULT_HOLD_DAYS = 20


@dataclass(frozen=True)
class Trade:
    """One round-trip resulting from a backtest signal."""

    signal: BacktestSignal
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    hold_days: int
    truncated: bool = False  # True when we ran out of bars before hold_days

    @property
    def return_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return self.exit_price / self.entry_price - 1.0


@dataclass(frozen=True)
class BacktestReport:
    """Outcome of a full backtest run.

    ``trades`` are sorted by entry date ascending. Metrics live alongside
    so the report layer can render without recomputing.
    """

    signals: list[BacktestSignal]
    trades: list[Trade]
    metrics: Metrics
    by_pattern: dict[str, Metrics]
    by_sector: dict[str, Metrics]
    by_score_band: dict[str, Metrics]
    by_narrative_band: dict[str, Metrics] = field(default_factory=dict)
    qlib_metrics: dict[str, float] | None = None
    features_evaluation: "FeatureEvaluation | None" = None
    params: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------- #
# Public entry point                                                     #
# ---------------------------------------------------------------------- #


def run_backtest(
    symbols: list[str],
    *,
    start: str | date | pd.Timestamp,
    end: str | date | pd.Timestamp,
    min_score: float = 60.0,
    hold_days: int = DEFAULT_HOLD_DAYS,
    cooldown_days: int = 0,
    evaluate_features: bool = False,
    feature_horizon: int = 5,
) -> BacktestReport:
    """End-to-end backtest. One call from a CLI or notebook.

    Args:
        symbols: Tickers to scan and trade.
        start: First in-window bar (inclusive).
        end: Last in-window bar (inclusive).
        min_score: Pattern-score floor. Higher than the dashboard default
            (50) because the backtest report is most useful when it's
            focused on the matches we'd actually trade.
        hold_days: Forward-holding window in trading days.
        cooldown_days: Skip new signals on the same ``(symbol, pattern)``
            for this many bars after entry. ``0`` disables the cooldown,
            but multiple overlapping entries on the same symbol/pattern
            usually just inflate counts without changing conclusions.
        evaluate_features: When True, also run the feature-evaluation
            pipeline (Alpha158-lite + custom + sector-relative) over the
            same universe and window, computing per-feature IC. The
            result is attached to ``BacktestReport.features_evaluation``
            and surfaces in the markdown report.
        feature_horizon: Forward-return horizon (bars) used for the
            feature IC. Ignored when ``evaluate_features`` is False.

    Returns:
        A :class:`BacktestReport` with trades, metrics, breakdowns, and
        an optional feature evaluation.
    """
    signals = generate_signals(symbols, start=start, end=end, min_score=min_score)

    features_evaluation = None
    if evaluate_features:
        features_evaluation = _build_features(symbols, start, end, feature_horizon)

    if not signals:
        log.info("backtest produced no signals")
        return BacktestReport(
            signals=[], trades=[],
            metrics=compute_metrics([]),
            by_pattern={}, by_sector={}, by_score_band={},
            features_evaluation=features_evaluation,
            params=_params(start, end, min_score, hold_days, cooldown_days,
                           len(symbols), evaluate_features, feature_horizon),
        )

    frames = _refetch_frames(signals, start, end)
    trades = simulate_trades(
        signals, frames,
        hold_days=hold_days,
        cooldown_days=cooldown_days,
    )

    metrics = compute_metrics(trades)
    daily = daily_returns_from_trades(trades)
    qlib_metrics = compute_qlib_metrics(daily)

    return BacktestReport(
        signals=signals,
        trades=trades,
        metrics=metrics,
        by_pattern=breakdown_by(trades, "pattern"),
        by_sector=breakdown_by(trades, "sector"),
        by_score_band=breakdown_by_score_band(trades),
        qlib_metrics=qlib_metrics,
        features_evaluation=features_evaluation,
        params=_params(start, end, min_score, hold_days, cooldown_days,
                       len(symbols), evaluate_features, feature_horizon),
    )


def _build_features(symbols, start, end, horizon):
    """Local import so the optional feature stack stays optional."""
    try:
        from src.features import build_feature_evaluation
    except Exception as exc:  # pragma: no cover - import error path
        log.warning("feature evaluation skipped (import failed): %s", exc)
        return None
    try:
        return build_feature_evaluation(
            symbols, start=start, end=end, forward_horizon=horizon,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("feature evaluation failed: %s", exc)
        return None


# ---------------------------------------------------------------------- #
# Trade simulation                                                       #
# ---------------------------------------------------------------------- #


def simulate_trades(
    signals: list[BacktestSignal],
    frames: dict[str, pd.DataFrame],
    *,
    hold_days: int = DEFAULT_HOLD_DAYS,
    cooldown_days: int = 0,
) -> list[Trade]:
    """Convert signals to trades using ``frames`` for price lookups.

    Entry is the next bar's open after the signal date; exit is the
    close ``hold_days`` bars later (or the last available bar if the
    backtest ends first). Signals on a (symbol, pattern) re-enter only
    after the cooldown elapses.
    """
    # Sort once so we can scan forward predictably for cooldowns.
    signals = sorted(signals, key=lambda s: (s.symbol, s.pattern, s.date))
    trades: list[Trade] = []
    next_eligible: dict[tuple[str, str], pd.Timestamp] = {}

    for sig in signals:
        df = frames.get(sig.symbol)
        if df is None:
            continue

        key = (sig.symbol, sig.pattern)
        floor = next_eligible.get(key)
        if floor is not None and sig.date < floor:
            continue

        trade = _simulate_one(sig, df, hold_days=hold_days)
        if trade is None:
            continue
        trades.append(trade)
        if cooldown_days > 0:
            # Next eligible signal must be strictly after the exit + cooldown.
            next_eligible[key] = trade.exit_date + pd.Timedelta(days=cooldown_days)

    trades.sort(key=lambda t: t.entry_date)
    return trades


def _simulate_one(
    signal: BacktestSignal, df: pd.DataFrame, *, hold_days: int
) -> Trade | None:
    """Resolve a single signal to a trade. Returns None when entry isn't possible."""
    idx = df.index
    # Find the bar immediately after the signal date — that's our entry bar.
    entry_positions = idx.searchsorted(signal.date, side="right")
    if entry_positions >= len(idx):
        # Signal was on the last available bar; no forward bar to enter on.
        return None

    entry_pos = int(entry_positions)
    entry_bar = df.iloc[entry_pos]
    entry_price = float(entry_bar["open"])
    if not entry_price or pd.isna(entry_price):
        return None

    exit_pos = min(entry_pos + hold_days, len(idx) - 1)
    exit_bar = df.iloc[exit_pos]
    exit_price = float(exit_bar["close"])

    return Trade(
        signal=signal,
        entry_date=idx[entry_pos],
        entry_price=entry_price,
        exit_date=idx[exit_pos],
        exit_price=exit_price,
        hold_days=exit_pos - entry_pos,
        truncated=(exit_pos - entry_pos) < hold_days,
    )


# ---------------------------------------------------------------------- #
# Internals                                                              #
# ---------------------------------------------------------------------- #


def _refetch_frames(
    signals: list[BacktestSignal],
    start: str | date | pd.Timestamp,
    end: str | date | pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """Pull OHLCV for every symbol that produced a signal.

    The signal generator already fetched each symbol once; this call is
    almost always a cache hit. We re-fetch from the public API instead
    of plumbing the frames through so callers building signals by hand
    (e.g. tests) don't have to.
    """
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    lookback = (end_ts - start_ts).days + WARMUP_DAYS + 30
    out: dict[str, pd.DataFrame] = {}
    for symbol in sorted({s.symbol for s in signals}):
        try:
            out[symbol] = fetch_ohlcv(symbol, lookback_days=lookback, end_date=end_ts.date())
        except Exception as exc:
            log.warning("fetch failed for %s during simulation: %s", symbol, exc)
    return out


def _params(
    start, end, min_score, hold_days, cooldown_days, symbol_count,
    evaluate_features=False, feature_horizon=5,
) -> dict[str, object]:
    out: dict[str, object] = {
        "start": str(pd.Timestamp(start).date()),
        "end": str(pd.Timestamp(end).date()),
        "min_score": min_score,
        "hold_days": hold_days,
        "cooldown_days": cooldown_days,
        "symbol_count": symbol_count,
    }
    if evaluate_features:
        out["evaluate_features"] = True
        out["feature_horizon"] = feature_horizon
    return out
