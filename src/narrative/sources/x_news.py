"""X (Twitter) high-quality accounts news source.

Pulls recent posts from a curated list of high-signal accounts
(`src/narrative/sources/x_accounts.py`) that mention the ticker.

This is intended as a *moderate momentum / popularity booster*, not the
primary signal. Posts are given a small extra weight in the narrative
scorer when they contain recognized themes or catalysts.

The source is completely optional. If no X bearer token is configured
the `fetch()` method returns an empty list silently and the rest of the
narrative pipeline is unaffected.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from src.config import get_settings
from src.utils import get_logger

from .base import NewsItem
from .x_accounts import HIGH_QUALITY_X_ACCOUNTS

log = get_logger(__name__)

_NAME = "x"
_BASE = "https://api.twitter.com/2"
_MAX_RESULTS_PER_CALL = 10
_MAX_ACCOUNTS_PER_QUERY = 8  # keep individual queries under X length limits

# Short tickers that are also common English words. Using only the cashtag
# ($TICKER) for these avoids matching everyday language ("on the call",
# "arm of the company", "in the news", "cr earnings", "st guidance", etc.)
# in the free-text portion of tweets. NVDA and most others are safe for the
# bare OR cashtag form.
AMBIGUOUS_TICKERS: frozenset[str] = frozenset({
    "ON", "ARM", "J", "CR", "ST", "MP", "BA", "T", "D", "V", "MA", "IT",
    "OR", "IF", "BY", "AT", "IN", "BE", "DO", "GO", "SO", "UP", "NO",
})


def _symbol_clause(symbol: str) -> str:
    """Build the X query clause for a ticker, preferring cashtag for ambiguous ones."""
    sym = symbol.upper()
    if sym in AMBIGUOUS_TICKERS:
        return f"${sym}"
    return f"({sym} OR ${sym})"


class XAccountsNewsSource:
    """X/Twitter recent posts from curated high-quality accounts."""

    name = _NAME

    def __init__(self, *, timeout: int = 12) -> None:
        self.timeout = timeout

    def fetch(self, symbol: str, *, limit: int = 20) -> list[NewsItem]:
        settings = get_settings()
        if not settings.has_x:
            log.debug("x news skipped: no X_BEARER_TOKEN configured")
            return []

        bearer = settings.x_bearer_token
        if not bearer:
            return []

        symbol = symbol.upper()
        items: list[NewsItem] = []

        # Split accounts into small batches to keep query strings reasonable
        batches = _chunk(HIGH_QUALITY_X_ACCOUNTS, _MAX_ACCOUNTS_PER_QUERY)

        for batch in batches:
            try:
                batch_items = self._fetch_for_accounts(symbol, batch, bearer, limit)
                items.extend(batch_items)
            except Exception as exc:
                log.info("x news batch failed for %s: %s", symbol, exc)
                # continue with other batches

        # Dedup within this source and respect caller's limit
        seen: set[str] = set()
        deduped: list[NewsItem] = []
        for item in items:
            key = item.dedup_key()
            if key not in seen:
                seen.add(key)
                deduped.append(item)
            if len(deduped) >= limit:
                break

        return deduped

    def _fetch_for_accounts(
        self, symbol: str, accounts: list[str], bearer: str, limit: int
    ) -> list[NewsItem]:
        if not accounts:
            return []

        accounts_clause = " OR ".join(f"from:{u}" for u in accounts)
        symbol_clause = _symbol_clause(symbol)
        query = f"({accounts_clause}) {symbol_clause} -is:retweet"

        # Look back ~7 days (X recent search window on most tiers)
        start_time = (datetime.now(tz=timezone.utc) - timedelta(days=7)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        params = {
            "query": query,
            "max_results": min(_MAX_RESULTS_PER_CALL, limit),
            "start_time": start_time,
            "tweet.fields": "created_at,author_id,text",
            "expansions": "author_id",
            "user.fields": "username",
        }

        try:
            payload = _get(
                f"{_BASE}/tweets/search/recent",
                params=params,
                bearer=bearer,
                timeout=self.timeout,
            )
        except Exception as exc:
            log.info("x recent search failed for %s: %s", symbol, exc)
            return []

        return _parse_response(payload, symbol)


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential_jitter(initial=0.4, max=3),
    reraise=True,
)
def _get(url: str, params: dict, bearer: str, timeout: int) -> dict:
    headers = {
        "Authorization": f"Bearer {bearer}",
        "User-Agent": "intelligent-stock-scanner",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    if resp.status_code in (401, 403):
        raise RuntimeError(f"x unauthorized or forbidden ({resp.status_code})")
    if resp.status_code == 429:
        raise RuntimeError("x rate limited")
    resp.raise_for_status()
    return resp.json()


def _parse_response(payload: dict[str, Any], requested_symbol: str) -> list[NewsItem]:
    data = payload.get("data") or []
    includes = payload.get("includes") or {}
    users = includes.get("users") or []

    # author_id -> username map
    user_map = {u.get("id"): u.get("username", "") for u in users if u.get("id")}

    items: list[NewsItem] = []
    for tweet in data:
        try:
            item = _parse_tweet(tweet, user_map, requested_symbol)
            if item:
                items.append(item)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("skipping malformed x post: %s", exc)
    return items


def _parse_tweet(
    tweet: dict[str, Any], user_map: dict[str, str], requested_symbol: str
) -> NewsItem | None:
    text = (tweet.get("text") or "").strip()
    if not text:
        return None

    created_at = tweet.get("created_at")
    published = _parse_iso(created_at)

    author_id = tweet.get("author_id", "")
    username = user_map.get(author_id, "unknown")

    tweet_id = tweet.get("id", "")
    url = f"https://x.com/{username}/status/{tweet_id}" if tweet_id else ""

    # For posts we use the tweet text for both title and summary.
    # The narrative layer's theme/catalyst detectors + short length work well.
    title = text[:140].replace("\n", " ").strip()
    summary = text.replace("\n", " ").strip()

    return NewsItem(
        title=title,
        summary=summary,
        url=url,
        published_utc=published,
        provider=_NAME,
        publisher=f"X / @{username}",
        tickers=(requested_symbol,),
        external_sentiment=None,  # we let our lexicon + themes do the work
        raw=tweet,
    )


def _parse_iso(ts: str | None) -> datetime:
    if not ts:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        # X returns e.g. "2025-01-15T14:22:00.000Z"
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(tz=timezone.utc)


def _chunk(seq: list[str], size: int) -> list[list[str]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


# ---------------------------------------------------------------------- #
# Task 5: unit test for the private clause builder (runs under pytest)
# ---------------------------------------------------------------------- #


def test_symbol_clause_prefers_cashtag_for_ambiguous():
    """ARM (ambiguous English word) must use only $ARM; NVDA uses both forms."""
    assert _symbol_clause("ARM") == "$ARM"
    assert _symbol_clause("arm") == "$ARM"
    assert _symbol_clause("NVDA") == "(NVDA OR $NVDA)"
    assert "NVDA OR $NVDA" in _symbol_clause("NVDA")
    # sanity for a couple more from the set
    assert _symbol_clause("ON") == "$ON"
    assert _symbol_clause("BA") == "$BA"
