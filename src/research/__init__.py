"""Deep research layer (scaffold).

A secondary validation layer the Stock Finder Agent will call only on the
highest-conviction matches surfaced by the scanner + narrative pipeline.
Importing this package is cheap — it defines protocols and a no-op
baseline only. Real implementations (LLM-driven research, manual notes,
external data vendors) plug in by satisfying the :class:`Researcher`
protocol.

The mission positions deep research as *supporting* — not a gate on
idea generation. The scanner remains fast and primary; this layer is
opt-in and runs against a short list of top matches.
"""

from __future__ import annotations

from .base import (
    DEFAULT_CONVICTION_THRESHOLD,
    NullResearcher,
    ResearchResult,
    Researcher,
    should_research,
    top_candidates,
)
from .llm_researcher import (
    DEFAULT_MAX_HEADLINES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    LLMResearcher,
)

__all__ = [
    "DEFAULT_CONVICTION_THRESHOLD",
    "DEFAULT_MAX_HEADLINES",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "LLMResearcher",
    "NullResearcher",
    "ResearchResult",
    "Researcher",
    "should_research",
    "top_candidates",
]
