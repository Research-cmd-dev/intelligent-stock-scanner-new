"""yfinance news source.

``yf.Ticker(symbol).news`` has changed shape across releases. We handle
both layouts seen in the wild:

  * Modern (yfinance ≥ 0.2.40):
      ``{"id": ..., "content": {"title": ..., "summary": ...,
         "pubDate": "ISO", "provider": {"displayName": ...},
         "canonicalUrl": {"url": ...}}}``
  * Legacy (yfinance < 0.2.40):
      ``{"title": ..., "publisher": ..., "link": ...,
         "providerPublishTime": 1700000000, "relatedTickers": [...]}``

If the upstream library changes again we degrade gracefully — missing
fields default to empty strings / epoch-zero timestamps and the scorer
just sees fewer items, not a crash.
"""

from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf

from src.utils import get_logger

from .base import NewsItem

log = get_logger(__name__)

_NAME = "yfinance"


class YFinanceNewsSource:
    """yfinance news adapter."""

    name = _NAME

    def fetch(self, symbol: str, *, limit: int = 20) -> list[NewsItem]:
        try:
            ticker = yf.Ticker(symbol)
            raw = ticker.news or []
        except Exception as exc:
            log.info("yfinance news fetch failed for %s: %s", symbol, exc)
            return []

        items: list[NewsItem] = []
        for entry in raw[:limit]:
            try:
                item = _parse(entry, symbol)
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("skipping malformed yfinance item: %s", exc)
                continue
            if item.title:
                items.append(item)
        return items


def _parse(entry: dict, symbol: str) -> NewsItem:
    # New-shape entries wrap everything under "content"; old-shape is flat.
    content = entry.get("content") if isinstance(entry.get("content"), dict) else None

    if content:
        title = (content.get("title") or "").strip()
        summary = (content.get("summary") or content.get("description") or "").strip()
        url = _first_url(content)
        publisher = (content.get("provider") or {}).get("displayName", "") or ""
        published = _parse_pub_date(content.get("pubDate") or content.get("displayTime"))
        tickers = tuple(
            (content.get("finance") or {}).get("stockTickers", [])
            or [symbol.upper()]
        )
    else:
        title = (entry.get("title") or "").strip()
        summary = ""
        url = entry.get("link") or ""
        publisher = entry.get("publisher", "") or ""
        published = _parse_unix(entry.get("providerPublishTime"))
        tickers = tuple(entry.get("relatedTickers") or [symbol.upper()])

    return NewsItem(
        title=title,
        summary=summary,
        url=url,
        published_utc=published,
        provider=_NAME,
        publisher=publisher,
        tickers=tickers,
        external_sentiment=None,  # yfinance ships no sentiment field
        raw=entry,
    )


def _first_url(content: dict) -> str:
    for key in ("canonicalUrl", "clickThroughUrl"):
        v = content.get(key)
        if isinstance(v, dict) and v.get("url"):
            return v["url"]
        if isinstance(v, str) and v:
            return v
    return ""


def _parse_pub_date(value: object) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.fromtimestamp(0, tz=timezone.utc)
    if isinstance(value, (int, float)):
        return _parse_unix(value)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _parse_unix(value: object) -> datetime:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)
