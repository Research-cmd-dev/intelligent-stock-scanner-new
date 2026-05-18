"""Shared types for news sources.

A *news source* is anything that can produce a list of :class:`NewsItem`
for a ticker. The :class:`NewsSource` protocol is intentionally tiny so
we can add wire services, RSS feeds, or LLM-summarized aggregators later
without touching the scorer.

Every source is allowed (and expected) to fail silently — network hiccups,
auth issues, missing data should return ``[]`` and log a warning, never
raise. A failing news source must not abort a scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class NewsItem:
    """One normalized news article.

    ``provider`` is the source platform we pulled it from (``"polygon"``,
    ``"yfinance"``). ``publisher`` is the actual outlet (Reuters, CNBC,
    Bloomberg). The distinction matters for dedup: two providers often
    surface the same publisher's story.

    ``external_sentiment`` is populated when the upstream API ships its
    own sentiment label (Polygon's ``insights[]``). The narrative layer
    prefers this signal over the in-house lexicon when it's present.
    """

    title: str
    summary: str
    url: str
    published_utc: datetime
    provider: str
    publisher: str
    tickers: tuple[str, ...] = ()
    external_sentiment: float | None = None  # -1..+1 if upstream gave us one
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    def dedup_key(self) -> str:
        """Stable key used to merge duplicates across providers.

        Two articles with the same URL are the same story. If URLs differ
        (e.g. Yahoo wrapper vs. publisher direct), we fall back to a
        normalized title — lowercased, alphanumerics only, capped at
        80 chars so minor punctuation/whitespace variation doesn't split
        the same headline into two items.
        """
        if self.url:
            return self.url.split("?", 1)[0].rstrip("/").lower()
        normalized = "".join(ch for ch in self.title.lower() if ch.isalnum())
        return normalized[:80]


class NewsSource(Protocol):
    """Callable that fetches news for one ticker.

    Implementations should:
      * Return an empty list on failure (log, don't raise).
      * Respect ``limit`` as a soft cap.
      * Return items in any order — the scorer sorts by recency.
    """

    name: str

    def fetch(self, symbol: str, *, limit: int = 20) -> list[NewsItem]: ...


def utc_now() -> datetime:
    """Aware UTC ``datetime`` for "now". Centralized for test patching."""
    return datetime.now(tz=timezone.utc)
