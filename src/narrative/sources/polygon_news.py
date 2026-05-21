"""Polygon.io News API source.

Endpoint: ``GET /v2/reference/news?ticker=<sym>&limit=...``

Polygon ships a curated insights block on most articles that includes a
sentiment label per ticker. We surface that as ``external_sentiment`` so
the scorer can prefer it over the in-house lexicon — it's hand-checked
upstream and meaningfully more accurate than word counts.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from src.config import get_settings
from src.utils import get_logger

from .base import NewsItem

log = get_logger(__name__)

_BASE = "https://api.polygon.io"
_NAME = "polygon"

# Map Polygon's three string labels onto a -1..+1 numeric scale.
_SENTIMENT_MAP = {"positive": 0.7, "neutral": 0.0, "negative": -0.7}


class PolygonNewsSource:
    """Polygon News API client."""

    name = _NAME

    def __init__(self, *, timeout: int = 10) -> None:
        self.timeout = timeout

    def fetch(self, symbol: str, *, limit: int = 20) -> list[NewsItem]:
        settings = get_settings()
        if not settings.has_polygon:
            log.debug("polygon news skipped: no API key")
            return []
        try:
            payload = _get(
                f"{_BASE}/v2/reference/news",
                params={
                    "ticker": symbol.upper(),
                    "limit": min(limit, 50),
                    "order": "desc",
                    "sort": "published_utc",
                    "apiKey": settings.polygon_api_key,
                },
                timeout=self.timeout,
            )
        except Exception as exc:
            log.info("polygon news fetch failed for %s: %s", symbol, exc)
            return []

        results = payload.get("results") or []
        items: list[NewsItem] = []
        for r in results:
            try:
                items.append(_parse(r, symbol))
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("skipping malformed polygon item: %s", exc)
        return items


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential_jitter(initial=0.5, max=4),
    reraise=True,
)
def _get(url: str, params: dict, timeout: int) -> dict:
    resp = requests.get(url, params=params, timeout=timeout)
    if resp.status_code in (401, 403):
        raise RuntimeError(f"polygon news unauthorized ({resp.status_code})")
    if resp.status_code == 429:
        raise RuntimeError("polygon news rate-limited")
    resp.raise_for_status()
    return resp.json()


def _parse(r: dict, requested_symbol: str) -> NewsItem:
    published = _parse_iso(r.get("published_utc", ""))
    publisher = (r.get("publisher") or {}).get("name") or ""
    sentiment = _extract_sentiment(r.get("insights") or [], requested_symbol)
    return NewsItem(
        title=r.get("title", "").strip(),
        summary=(r.get("description") or "").strip(),
        url=r.get("article_url", "") or "",
        published_utc=published,
        provider=_NAME,
        publisher=publisher,
        tickers=tuple(r.get("tickers") or ()),
        external_sentiment=sentiment,
        raw=r,
    )


def _extract_sentiment(insights: list[dict], symbol: str) -> float | None:
    """Pull the sentiment for the requested ticker out of the insights array.

    Polygon may attach insights for several tickers on the same article;
    we want the one for the symbol we asked about. Falls back to the
    first insight if there is no per-ticker match.
    """
    if not insights:
        return None
    target = symbol.upper()
    chosen = next(
        (i for i in insights if (i.get("ticker") or "").upper() == target),
        insights[0],
    )
    label = (chosen.get("sentiment") or "").strip().lower()
    return _SENTIMENT_MAP.get(label)


def _parse_iso(s: str) -> datetime:
    # Polygon emits RFC3339 with a trailing Z; fromisoformat needs +00:00.
    # NewsItem.published_utc is always tz-aware by contract (see x_news.py too).
    if not s:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
