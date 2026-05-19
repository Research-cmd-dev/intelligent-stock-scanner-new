"""Modal compute layer.

This package is **opt-in**. The core scanner, narrative layer, and
Streamlit dashboard never import from here, so a Codespaces user who
never installs ``modal`` is completely unaffected.

What lives here:

* :mod:`src.modal_app.app` — the Modal ``App``, container ``Image``,
  the persistent ``stock_data`` ``Volume``, and the
  ``download_universe_remote`` / ``run_backtest_remote`` functions.
* :data:`AVAILABLE_TOOLS` — a registry describing each remote function
  in JSON-schema-friendly form, designed so a future LangGraph /
  LLM-driven planner can register them as tools without touching this
  package's internals.

Importing this module never imports ``modal`` at top level. The
``modal`` import is lazy in :mod:`src.modal_app.app` so that scripts
that only consume the tool catalog (for instance, the future agent
that builds a Claude tools list) can import safely on machines without
the Modal client installed.
"""

from __future__ import annotations

from typing import Any

# Static catalog of remote-callable tools. The first consumer is the
# CLI / docs, the next will be the intelligence layer.
AVAILABLE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "download_historical_data",
        "summary": "Refresh the persistent stock_data volume with OHLCV history.",
        "function_path": "src.modal_app.app:download_universe_remote",
        "parameters": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tickers to refresh. Use sector helpers to expand.",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Re-download full history even if local copy exists.",
                },
                "max_workers": {
                    "type": "integer",
                    "default": 8,
                    "description": "Parallel HTTP workers inside the Modal container.",
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "run_backtest",
        "summary": "Run the full backtest + feature evaluation on Modal using volume data.",
        "function_path": "src.modal_app.app:run_backtest_remote",
        "parameters": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Universe for the backtest.",
                },
                "start": {"type": "string", "description": "First in-window bar YYYY-MM-DD."},
                "end": {"type": "string", "description": "Last in-window bar YYYY-MM-DD."},
                "min_score": {"type": "number", "default": 60.0},
                "hold_days": {"type": "integer", "default": 20},
                "cooldown_days": {"type": "integer", "default": 0},
                "evaluate_features": {"type": "boolean", "default": False},
                "feature_horizon": {"type": "integer", "default": 5},
            },
            "required": ["symbols", "start", "end"],
        },
    },
]


def available_tools() -> list[dict[str, Any]]:
    """Return the tool catalog. Designed for an LLM tool-binding step."""
    return list(AVAILABLE_TOOLS)


__all__ = ["AVAILABLE_TOOLS", "available_tools"]
