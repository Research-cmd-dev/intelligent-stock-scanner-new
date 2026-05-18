"""Performance metrics for a list of trades.

Two layers:

- :func:`compute_metrics` — pure aggregate stats over a trade list.
- :func:`compute_qlib_metrics` — same metrics, routed through Qlib's
  ``risk_analysis`` when Qlib is importable. Returns ``None`` otherwise.

The dashboard / report layer can call :func:`compute_metrics` directly
and treat the optional Qlib output as an enrichment, so the system
never hard-fails on a missing Qlib install.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import Trade

# Roughly the number of US trading days per year. Used to annualize daily
# return statistics; consistent with Qlib's convention.
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class Metrics:
    """Aggregate trade statistics.

    All return fields are decimals (0.05 == 5%). ``profit_factor`` is
    ``gross_profit / gross_loss`` and is ``inf`` when there are no losers,
    ``0.0`` when there are no winners.
    """

    trade_count: int
    win_rate: float                 # fraction in [0, 1]
    mean_return: float
    median_return: float
    total_return: float             # sum of trade returns (additive, not compounded)
    profit_factor: float
    max_drawdown: float             # negative number; -0.20 = -20%
    sharpe_like: float              # mean / std of trade returns × sqrt(N)
    best_trade: float
    worst_trade: float
    avg_hold_days: float

    def to_row(self) -> dict[str, object]:
        return {
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 3),
            "mean_return_pct": round(self.mean_return * 100, 2),
            "median_return_pct": round(self.median_return * 100, 2),
            "total_return_pct": round(self.total_return * 100, 2),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown_pct": round(self.max_drawdown * 100, 2),
            "sharpe_like": round(self.sharpe_like, 2),
            "best_trade_pct": round(self.best_trade * 100, 2),
            "worst_trade_pct": round(self.worst_trade * 100, 2),
            "avg_hold_days": round(self.avg_hold_days, 1),
        }


def compute_metrics(trades: list["Trade"]) -> Metrics:
    """Aggregate trade stats. Returns a zeroed Metrics for an empty list."""
    if not trades:
        return Metrics(
            trade_count=0, win_rate=0.0, mean_return=0.0, median_return=0.0,
            total_return=0.0, profit_factor=0.0, max_drawdown=0.0,
            sharpe_like=0.0, best_trade=0.0, worst_trade=0.0,
            avg_hold_days=0.0,
        )

    returns = [t.return_pct for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)  # positive number

    if gross_loss == 0.0:
        pf = math.inf if gross_profit > 0 else 0.0
    else:
        pf = gross_profit / gross_loss

    return Metrics(
        trade_count=len(trades),
        win_rate=len(wins) / len(returns),
        mean_return=sum(returns) / len(returns),
        median_return=_median(returns),
        total_return=sum(returns),
        profit_factor=pf,
        max_drawdown=_max_drawdown(returns),
        sharpe_like=_sharpe_like(returns),
        best_trade=max(returns),
        worst_trade=min(returns),
        avg_hold_days=sum(t.hold_days for t in trades) / len(trades),
    )


def compute_qlib_metrics(daily_returns: list[float]) -> dict[str, float] | None:
    """Return Qlib's ``risk_analysis`` view of a daily return series.

    ``None`` when Qlib isn't installed — callers should treat this as
    "Qlib not available; rely on in-house metrics."
    """
    try:
        import pandas as pd
        from qlib.contrib.evaluate import risk_analysis
    except Exception:  # noqa: BLE001 - any import failure is "not available"
        return None

    if not daily_returns:
        return None

    series = pd.Series(daily_returns)
    df = risk_analysis(series, freq="day", N=TRADING_DAYS_PER_YEAR)
    # risk_analysis returns a DataFrame indexed by metric name with a
    # single "risk" column. Flatten into plain dict for the report layer.
    return {name: float(df.loc[name, "risk"]) for name in df.index}


def daily_returns_from_trades(
    trades: list["Trade"], capital_per_trade: float = 1.0
) -> list[float]:
    """Build a per-trade-day return series suitable for Sharpe / drawdown.

    Each trade contributes one row: its return spread evenly across its
    hold days. This isn't a portfolio simulation — it's a "what did each
    held bar earn on average" series, which is enough to feed Qlib's
    risk_analysis or our own Sharpe approximation.
    """
    out: list[float] = []
    for t in trades:
        hold = max(t.hold_days, 1)
        out.extend([t.return_pct / hold] * hold)
    return out


# ---------------------------------------------------------------------- #
# Breakdowns                                                             #
# ---------------------------------------------------------------------- #


def breakdown_by(
    trades: list["Trade"], key: str
) -> dict[str, Metrics]:
    """Group trades by an attribute of ``trade.signal`` and compute metrics per group.

    ``key`` accepts ``"pattern"`` or ``"sector"``. Sectors fan out: a
    multi-sector signal contributes to each of its sector groups, so
    breakdowns are not mutually exclusive — they're "performance when a
    trade touched this label."
    """
    buckets: dict[str, list["Trade"]] = {}
    for t in trades:
        labels = _labels_for(t, key)
        for label in labels:
            buckets.setdefault(label, []).append(t)
    return {label: compute_metrics(ts) for label, ts in buckets.items()}


def breakdown_by_score_band(
    trades: list["Trade"],
    bands: tuple[tuple[float, float], ...] = ((50, 60), (60, 70), (70, 80), (80, 101)),
) -> dict[str, Metrics]:
    """Bucket trades by pattern-score band — the canonical refinement signal.

    The default bands map to the rules of thumb in CLAUDE.md (50-70 worth
    a look, 70+ clean). Last band ends at 101 so a perfect 100 still falls
    inside the top bucket.
    """
    buckets: dict[str, list["Trade"]] = {}
    for t in trades:
        for lo, hi in bands:
            if lo <= t.signal.score < hi:
                buckets.setdefault(f"{int(lo)}-{int(hi - 1)}", []).append(t)
                break
    return {label: compute_metrics(ts) for label, ts in buckets.items()}


def _labels_for(trade: "Trade", key: str) -> list[str]:
    sig = trade.signal
    if key == "pattern":
        return [sig.pattern]
    if key == "sector":
        return list(sig.sectors) if sig.sectors else ["(unclassified)"]
    raise ValueError(f"unknown breakdown key: {key!r}")


# ---------------------------------------------------------------------- #
# Numeric helpers                                                        #
# ---------------------------------------------------------------------- #


def _median(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    s = sorted(values)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _max_drawdown(returns: list[float]) -> float:
    """Peak-to-trough drawdown over the cumulative-sum equity curve.

    We sum (not compound) trade returns so the curve is robust to the
    trade-ordering assumption (it's a flat equity series, not a real
    portfolio sim). Result is in ``[-1, 0]``.
    """
    if not returns:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        dd = equity - peak  # ≤ 0
        max_dd = min(max_dd, dd)
    return max_dd


def _sharpe_like(returns: list[float]) -> float:
    """Per-trade Sharpe ratio scaled by sqrt(N). Not annualized.

    Returns 0.0 when fewer than 2 trades or zero variance.
    """
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if var == 0.0:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(len(returns))
