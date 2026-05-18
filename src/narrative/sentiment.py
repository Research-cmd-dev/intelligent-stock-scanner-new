"""Sentiment scoring for news items.

Two strategies live here:

  * :class:`LexiconSentiment` — fast, offline, deterministic. Counts
    positive vs. negative hits from :mod:`src.narrative.lexicon`.
  * Any future :class:`LLMSentiment` — same protocol, different engine.

Both implement the :class:`Sentiment` protocol, so the
:class:`NarrativeScorer` swaps them with one constructor argument.

The aggregate score returned by :func:`score_item` always falls in
``[-1.0, +1.0]``. The scorer module is what maps that into the 0-1
narrative score used downstream.
"""

from __future__ import annotations

import re
from typing import Protocol

from .lexicon import (
    NEGATIVE_PHRASES, NEGATIVE_WORDS,
    POSITIVE_PHRASES, POSITIVE_WORDS,
)
from .sources.base import NewsItem

# Token boundary: alphanumerics + hyphens; everything else splits.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")


class Sentiment(Protocol):
    """Anything that can score a single news item to ``[-1, +1]``."""

    name: str

    def score_item(self, item: NewsItem) -> float: ...


class LexiconSentiment:
    """Word + phrase counting against the bundled finance lexicon.

    Score formula (per item):

        polarity = (pos_hits - neg_hits) / max(pos_hits + neg_hits, 1)

    Returns 0.0 when no lexicon hit is found at all — that's "we have
    nothing to say about this article" rather than "we know it's neutral",
    and the scorer handles the distinction when aggregating.
    """

    name = "lexicon"

    def __init__(self, *, prefer_external: bool = True) -> None:
        # When True, an item that already carries an external_sentiment
        # (Polygon's insights) bypasses the lexicon. This is on by default
        # because hand-curated upstream labels beat word counts.
        self.prefer_external = prefer_external

    def score_item(self, item: NewsItem) -> float:
        if self.prefer_external and item.external_sentiment is not None:
            return float(item.external_sentiment)
        text = _normalize(f"{item.title}. {item.summary}")
        if not text:
            return 0.0
        tokens = set(_TOKEN_RE.findall(text))
        pos = len(tokens & POSITIVE_WORDS) + _count_phrases(text, POSITIVE_PHRASES)
        neg = len(tokens & NEGATIVE_WORDS) + _count_phrases(text, NEGATIVE_PHRASES)
        if pos == 0 and neg == 0:
            return 0.0
        return (pos - neg) / (pos + neg)


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace + normalize hyphens to spaces.

    Phrases in the lexicon are stored in this normalized form, so the
    substring match in :func:`_count_phrases` works without surprises.
    """
    return " ".join(text.lower().replace("-", " ").split())


def _count_phrases(text: str, phrases: tuple[str, ...]) -> int:
    return sum(1 for p in phrases if p in text)
