"""Per-symbol, per-day disk cache for fetched news items.

A discovery scan can ask 100+ symbols for news; both Polygon and yfinance
are rate-limited and slow on cold calls. We cache the *combined* list of
:class:`NewsItem`\\s under ``data/cache/news/{SYMBOL}_{YYYY-MM-DD}.json``
and consider a cache fresh for the rest of the UTC calendar day. This
mirrors the OHLCV cache contract in ``src/data/fetcher.py``.

The cache is best-effort: on any read/write error we log and fall back
to a live fetch. We never raise from this module.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_settings
from src.utils import get_logger

from .base import NewsItem

log = get_logger(__name__)


def _today() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _cache_dir() -> Path:
    path = get_settings().cache_dir / "news"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(symbol: str) -> Path:
    """Path the cache would write today for ``symbol``."""
    return _cache_dir() / f"{symbol.upper()}_{_today()}.json"


def read(symbol: str) -> list[NewsItem] | None:
    """Return today's cached items for ``symbol``, or ``None`` on miss."""
    path = cache_path(symbol)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        log.warning("news cache read failed for %s: %s", symbol, exc)
        return None
    try:
        return [_from_dict(d) for d in payload]
    except Exception as exc:
        log.warning("news cache decode failed for %s: %s", symbol, exc)
        return None


def write(symbol: str, items: list[NewsItem]) -> None:
    """Persist ``items`` as today's cache for ``symbol``."""
    path = cache_path(symbol)
    try:
        path.write_text(json.dumps([_to_dict(i) for i in items], default=str))
    except Exception as exc:  # pragma: no cover - disk issues
        log.warning("news cache write failed for %s: %s", symbol, exc)


def _to_dict(item: NewsItem) -> dict:
    d = asdict(item)
    d["published_utc"] = item.published_utc.isoformat()
    # Strip the raw blob — it can contain large image dicts and is only
    # useful for debugging the upstream fetch, not for replay.
    d.pop("raw", None)
    return d


def _from_dict(d: dict) -> NewsItem:
    return NewsItem(
        title=d.get("title", ""),
        summary=d.get("summary", ""),
        url=d.get("url", ""),
        published_utc=datetime.fromisoformat(d["published_utc"]),
        provider=d.get("provider", ""),
        publisher=d.get("publisher", ""),
        tickers=tuple(d.get("tickers") or ()),
        external_sentiment=d.get("external_sentiment"),
    )
