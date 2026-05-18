"""Backtesting + self-refinement layer.

Closes the loop between the live scanner and historical performance.

Public API:

    from src.backtest import run_backtest, BacktestReport

    report = run_backtest(
        symbols=["NVDA", "PLTR", ...],
        start="2024-01-01",
        end="2026-05-01",
    )

The package is intentionally light on dependencies. Qlib's
``risk_analysis`` is used for industry-standard metrics when ``qlib``
is importable; otherwise the same metrics are computed in-house. Either
way the public ``BacktestReport`` looks the same.
"""

from __future__ import annotations

from .engine import BacktestReport, Trade, run_backtest
from .refine import Suggestion, suggest_improvements
from .signals import BacktestSignal, generate_signals

__all__ = [
    "BacktestReport",
    "BacktestSignal",
    "Suggestion",
    "Trade",
    "generate_signals",
    "run_backtest",
    "suggest_improvements",
]
