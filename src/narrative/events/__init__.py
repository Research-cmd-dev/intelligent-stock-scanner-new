"""Structured narrative events.

This package holds the source-indifferent ``(symbol, theme, polarity,
published_utc, ...)`` event store that bridges the transcript corpus
(and, eventually, news / social sources) to the scanner's
``BacktestSignal.narrative_score``.

Phase 4 ships the schema only. Phase 5 fills the table from transcript
chunks; Phase 6 wires the store into the backtest path. See
``docs/specs/PHASE_4_NARRATIVE_EVENTS.md``.
"""

from __future__ import annotations

from .schema import NarrativeEvent

__all__ = ["NarrativeEvent"]
