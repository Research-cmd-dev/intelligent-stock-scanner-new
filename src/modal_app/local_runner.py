"""Thin local-side helper for invoking remote Modal functions.

Why this exists: ``src.modal_app.app`` imports ``modal`` at top level
(decorators need it). Code that *triggers* remote calls — the
dashboard's optional Modal panel, a future LangGraph agent — should
not pay that import cost unless it is actually about to call out. This
module is the lazy bridge: import it cheaply, call into it only when
the user opts in.

Functions here mirror :data:`src.modal_app.AVAILABLE_TOOLS` 1:1 so the
intelligence layer can register them as tools with one line:

    from src.modal_app import available_tools
    from src.modal_app import local_runner

    tools = {t["name"]: getattr(local_runner, t["name"]) for t in available_tools()}
"""

from __future__ import annotations

from typing import Any


def _load_remote_functions():
    """Import the Modal app on demand and return the remote callables."""
    from .app import (
        clear_historical_remote,
        download_max_history_remote,
        download_universe_remote,
        run_backtest_remote,
    )
    return download_universe_remote, run_backtest_remote, clear_historical_remote, download_max_history_remote


def download_historical_data(
    symbols: list[str],
    *,
    force: bool = False,
    max_workers: int = 16,
) -> dict[str, Any]:
    """Synchronously trigger ``download_universe_remote`` on Modal."""
    dl, _, _, _ = _load_remote_functions()
    return dl.remote(symbols, force=force, max_workers=max_workers)


def run_backtest(
    symbols: list[str],
    *,
    start: str,
    end: str,
    min_score: float = 60.0,
    hold_days: int = 20,
    cooldown_days: int = 0,
    evaluate_features: bool = False,
    feature_horizon: int = 5,
    refresh: bool = True,
) -> dict[str, Any]:
    """Synchronously trigger ``run_backtest_remote`` on Modal."""
    _, bt, _, _ = _load_remote_functions()
    return bt.remote(
        symbols,
        start=start,
        end=end,
        min_score=min_score,
        hold_days=hold_days,
        cooldown_days=cooldown_days,
        evaluate_features=evaluate_features,
        feature_horizon=feature_horizon,
        refresh=refresh,
    )


def clear_historical_data() -> dict[str, Any]:
    """Synchronously trigger deletion of all historical data on the Modal volume."""
    _, _, clr, _ = _load_remote_functions()
    return clr.remote()


def download_max_history(
    force: bool = False,
    max_workers: int = 8,
) -> dict[str, Any]:
    """Synchronously trigger the full max-history yfinance pull (with dividends/splits)
    for the new SECTOR_TICKERS list into the Modal volume.
    """
    _, _, _, dlmax = _load_remote_functions()
    return dlmax.remote(force=force, max_workers=max_workers)


__all__ = [
    "download_historical_data",
    "run_backtest",
    "clear_historical_data",
    "download_max_history",
]
