"""Narrative scoring: aggregate news → 0-1 score + plain-English explanation.

The :class:`NarrativeScorer` is the single public entry into this layer.
It owns:

    1. **Fan-out fetch** — call every configured news source for the
       symbol (Polygon, yfinance, …). Failures on any one source are
       logged and dropped; the run continues with whatever returned.
    2. **Dedup + filter** — collapse duplicates across sources using a
       URL/title key, drop items older than ``max_age_days``.
    3. **Score** — each item gets sentiment in ``[-1, +1]``; we weight
       by recency (linear decay) and aggregate to a single ``[-1, +1]``
       polarity, then map to ``[0, 1]``.
    4. **Explain** — build a short, structured sentence the dashboard
       can show next to the chart.

Composite blending (pattern + narrative) lives in :func:`blend_composite`
so the Scanner can call it without taking a hard dependency on the
NarrativeScorer's defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.utils import get_logger

from .sentiment import LexiconSentiment, Sentiment
from .sources import NewsItem, NewsSource, default_sources
from .sources import cache as news_cache
from .sources.base import utc_now

log = get_logger(__name__)


# Default blend: 80% pattern, 20% narrative — overridable per scan.
DEFAULT_NARRATIVE_WEIGHT = 0.20


@dataclass(frozen=True)
class NarrativeResult:
    """Aggregate narrative read for one ticker.

    ``score`` is in ``[0, 1]`` (0.5 = neutral / no signal). ``polarity``
    is the underlying ``[-1, +1]`` aggregate we map from. ``top_items``
    are the items that contributed most to the score (positive *or*
    negative), capped at three for display.
    """

    ticker: str
    score: float
    polarity: float
    explanation: str
    item_count: int
    sources_used: tuple[str, ...]
    publishers: tuple[str, ...]
    top_items: tuple[NewsItem, ...]
    as_of: datetime
    cache_hit: bool = False

    def to_row(self) -> dict[str, object]:
        """Flat dict for table rendering."""
        return {
            "narrative_score": round(self.score, 3),
            "narrative_polarity": round(self.polarity, 3),
            "narrative_items": self.item_count,
            "narrative_sources": ", ".join(self.sources_used),
            "narrative_explanation": self.explanation,
        }


class NarrativeScorer:
    """Pull news from multiple sources, score it, explain it.

    Args:
        sources: News sources to query. Defaults to ``default_sources()``
            (Polygon + yfinance). Pass an explicit list to add or
            replace sources, or use ``[]`` to disable news entirely (the
            scorer then returns a neutral result for every ticker).
        sentiment: Sentiment engine. Defaults to :class:`LexiconSentiment`.
            Swap to a future LLM-backed engine here.
        max_age_days: Items older than this are dropped before scoring.
        max_items_per_source: Soft cap on items requested per source.
        use_cache: Read/write the per-day disk cache to avoid hammering
            APIs during a scan.
        recency_half_life_days: Recency-decay half-life used when
            weighting items in the aggregate. Older items contribute
            proportionally less.
    """

    def __init__(
        self,
        sources: list[NewsSource] | None = None,
        *,
        sentiment: Sentiment | None = None,
        max_age_days: int = 14,
        max_items_per_source: int = 15,
        use_cache: bool = True,
        recency_half_life_days: float = 5.0,
    ) -> None:
        self.sources = sources if sources is not None else default_sources()
        self.sentiment = sentiment or LexiconSentiment()
        self.max_age_days = max_age_days
        self.max_items_per_source = max_items_per_source
        self.use_cache = use_cache
        self.recency_half_life_days = recency_half_life_days

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def score(self, ticker: str) -> NarrativeResult:
        """Fetch, score, and explain news for one ticker."""
        symbol = ticker.upper()
        now = utc_now()

        cache_hit = False
        items: list[NewsItem] | None = (
            news_cache.read(symbol) if self.use_cache else None
        )
        if items is None:
            items = self._fetch_all(symbol)
            if self.use_cache:
                news_cache.write(symbol, items)
        else:
            cache_hit = True

        items = self._filter_recent(items, now)
        items = _dedup(items)
        items.sort(key=lambda i: i.published_utc, reverse=True)

        scored = [(i, self.sentiment.score_item(i)) for i in items]
        polarity = _aggregate_polarity(
            scored, now=now, half_life_days=self.recency_half_life_days
        )
        score = _polarity_to_score(polarity, item_count=len(items))
        top = _pick_top(scored, k=3)

        return NarrativeResult(
            ticker=symbol,
            score=score,
            polarity=polarity,
            explanation=_render_explanation(symbol, scored, polarity, now),
            item_count=len(items),
            sources_used=tuple(sorted({i.provider for i in items})),
            publishers=tuple(sorted({i.publisher for i in items if i.publisher})),
            top_items=top,
            as_of=now,
            cache_hit=cache_hit,
        )

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _fetch_all(self, symbol: str) -> list[NewsItem]:
        out: list[NewsItem] = []
        for src in self.sources:
            try:
                items = src.fetch(symbol, limit=self.max_items_per_source)
            except Exception as exc:
                log.warning("news source %s failed for %s: %s", src.name, symbol, exc)
                continue
            out.extend(items)
        return out

    def _filter_recent(
        self, items: list[NewsItem], now: datetime
    ) -> list[NewsItem]:
        cutoff = now - timedelta(days=self.max_age_days)
        return [i for i in items if _ensure_aware(i.published_utc) >= cutoff]


# ---------------------------------------------------------------------- #
# Composite blending                                                     #
# ---------------------------------------------------------------------- #


def blend_composite(
    pattern_score: float,
    narrative: NarrativeResult | None,
    *,
    narrative_weight: float = DEFAULT_NARRATIVE_WEIGHT,
) -> float:
    """Combine a 0-100 pattern score with a 0-1 narrative score.

    Linear blend: ``(1 - w) * pattern + w * narrative * 100``. A neutral
    narrative (0.5) pulls a strong pattern score toward 50 by ``w/2 * 100``,
    which is what we want — "no news / mixed news" should reduce
    conviction, just not by much when ``w`` is small.

    Pass ``narrative=None`` (or ``narrative_weight=0``) to disable the
    blend; the original pattern score is returned unchanged.
    """
    if narrative is None or narrative_weight <= 0.0:
        return float(pattern_score)
    if narrative_weight > 1.0:
        narrative_weight = 1.0
    return float(
        (1.0 - narrative_weight) * pattern_score
        + narrative_weight * narrative.score * 100.0
    )


# ---------------------------------------------------------------------- #
# Aggregation + explanation helpers                                      #
# ---------------------------------------------------------------------- #


def _dedup(items: list[NewsItem]) -> list[NewsItem]:
    seen: dict[str, NewsItem] = {}
    for item in items:
        key = item.dedup_key()
        if key in seen:
            # Promote external_sentiment if the duplicate carries one and
            # the kept item does not — losing that label is wasteful.
            if (
                seen[key].external_sentiment is None
                and item.external_sentiment is not None
            ):
                seen[key] = item
            continue
        seen[key] = item
    return list(seen.values())


def _aggregate_polarity(
    scored: list[tuple[NewsItem, float]],
    *,
    now: datetime,
    half_life_days: float,
) -> float:
    """Recency-weighted average polarity over scored items.

    Items contribute on a 2^(-age/half_life) curve, so a story from
    today weights ~4x a story from two half-lives ago. Items with zero
    sentiment (no lexicon hit, no external label) are still counted in
    the weight denominator so a flood of neutral coverage drags the
    aggregate toward zero rather than being silently ignored.
    """
    if not scored:
        return 0.0
    weighted_sum = 0.0
    weight_sum = 0.0
    for item, sentiment in scored:
        age_days = max(
            (now - _ensure_aware(item.published_utc)).total_seconds() / 86_400.0,
            0.0,
        )
        w = 0.5 ** (age_days / half_life_days)
        weighted_sum += w * sentiment
        weight_sum += w
    if weight_sum == 0.0:
        return 0.0
    return weighted_sum / weight_sum


def _polarity_to_score(polarity: float, *, item_count: int) -> float:
    """Map ``[-1, +1]`` polarity to ``[0, 1]`` narrative score.

    The midpoint 0.5 means "neutral / no signal". When we have very few
    items (<=2), we damp the score toward 0.5 — three positive headlines
    in a row is suggestive, but not enough to fully commit to a strong
    score.
    """
    raw = 0.5 + 0.5 * max(-1.0, min(1.0, polarity))
    if item_count == 0:
        return 0.5
    if item_count <= 2:
        return 0.5 + 0.5 * (raw - 0.5)  # half weight on thin coverage
    return raw


def _pick_top(
    scored: list[tuple[NewsItem, float]], *, k: int
) -> tuple[NewsItem, ...]:
    """Return the ``k`` items with the largest absolute sentiment.

    Falls back to most recent when nothing has a non-zero sentiment so
    the explanation still has something concrete to quote.
    """
    if not scored:
        return ()
    by_strength = sorted(scored, key=lambda s: abs(s[1]), reverse=True)
    nonzero = [pair for pair in by_strength if pair[1] != 0.0]
    chosen = (nonzero or by_strength)[:k]
    return tuple(item for item, _ in chosen)


def _render_explanation(
    symbol: str,
    scored: list[tuple[NewsItem, float]],
    polarity: float,
    now: datetime,
) -> str:
    """Compose a one-to-two sentence English summary of the news read."""
    if not scored:
        return f"No recent news for {symbol}."

    pos = sum(1 for _, s in scored if s > 0.15)
    neg = sum(1 for _, s in scored if s < -0.15)
    neutral = len(scored) - pos - neg
    tone = _tone_word(polarity)

    parts = [
        f"{len(scored)} recent article{'s' if len(scored) != 1 else ''}",
        f"tone {tone} ({pos}+ / {neg}- / {neutral}~)",
    ]

    top = _pick_top(scored, k=1)
    if top:
        item = top[0]
        age_days = max(
            (now - _ensure_aware(item.published_utc)).days, 0
        )
        when = "today" if age_days == 0 else f"{age_days}d ago"
        publisher = item.publisher or item.provider
        title = item.title.strip().rstrip(".")
        if len(title) > 90:
            title = title[:87].rstrip() + "..."
        parts.append(f'top: "{title}" ({publisher}, {when})')

    return "; ".join(parts) + "."


def _tone_word(polarity: float) -> str:
    if polarity > 0.35:
        return "bullish"
    if polarity > 0.10:
        return "leaning positive"
    if polarity < -0.35:
        return "bearish"
    if polarity < -0.10:
        return "leaning negative"
    return "mixed"


def _ensure_aware(dt: datetime) -> datetime:
    """Promote a naive datetime to UTC so arithmetic with ``utc_now`` works."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
