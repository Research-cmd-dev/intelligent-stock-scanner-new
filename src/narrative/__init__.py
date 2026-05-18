"""Narrative layer public API.

Importing this package is cheap — sources are constructed lazily inside
:class:`NarrativeScorer`, and the lexicon is a frozenset.
"""

from __future__ import annotations

from .scorer import (
    DEFAULT_NARRATIVE_WEIGHT, NarrativeResult, NarrativeScorer, blend_composite,
)
from .sentiment import LexiconSentiment, Sentiment
from .sources import NewsItem, NewsSource, default_sources

__all__ = [
    "DEFAULT_NARRATIVE_WEIGHT",
    "LexiconSentiment",
    "NarrativeResult",
    "NarrativeScorer",
    "NewsItem",
    "NewsSource",
    "Sentiment",
    "blend_composite",
    "default_sources",
]
