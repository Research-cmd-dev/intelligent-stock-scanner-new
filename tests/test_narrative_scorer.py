"""Tests for the NarrativeScorer aggregation pipeline."""

from __future__ import annotations

from src.narrative import NarrativeScorer, blend_composite
from src.narrative.scorer import _dedup
from tests.news_fixtures import (
    FakeSource, bearish_items, bullish_items, stale_items,
)


def test_scorer_returns_neutral_when_no_news() -> None:
    scorer = NarrativeScorer(sources=[FakeSource([])], use_cache=False)
    result = scorer.score("FAKE")
    assert result.score == 0.5
    assert result.item_count == 0
    assert "No recent news" in result.explanation


def test_scorer_bullish_news_gives_high_score() -> None:
    scorer = NarrativeScorer(sources=[FakeSource(bullish_items())], use_cache=False)
    result = scorer.score("FAKE")
    assert result.score > 0.65
    assert result.polarity > 0.30
    assert "bullish" in result.explanation or "positive" in result.explanation
    assert result.item_count == 3


def test_scorer_bearish_news_gives_low_score() -> None:
    scorer = NarrativeScorer(sources=[FakeSource(bearish_items())], use_cache=False)
    result = scorer.score("FAKE")
    assert result.score < 0.35
    assert result.polarity < -0.30
    assert "bearish" in result.explanation or "negative" in result.explanation


def test_scorer_drops_stale_items() -> None:
    scorer = NarrativeScorer(sources=[FakeSource(stale_items())], use_cache=False)
    result = scorer.score("FAKE")
    assert result.item_count == 0
    assert result.score == 0.5


def test_scorer_merges_multiple_sources_and_dedups() -> None:
    # Two sources surface the same items (same URLs) — should dedup.
    items = bullish_items()
    scorer = NarrativeScorer(
        sources=[FakeSource(items, name="a"), FakeSource(items, name="b")],
        use_cache=False,
    )
    result = scorer.score("FAKE")
    assert result.item_count == 3  # not 6


def test_dedup_prefers_external_sentiment_when_available() -> None:
    """When two providers surface the same URL, the one carrying an
    external sentiment label should win the dedup."""
    items = bullish_items()
    first = items[1]  # yfinance, no external sentiment
    duplicate = items[1].__class__(
        title=first.title, summary=first.summary, url=first.url,
        published_utc=first.published_utc, provider="polygon",
        publisher=first.publisher, tickers=first.tickers,
        external_sentiment=0.7,
    )
    # Important: the duplicate appears AFTER the lexicon-only version so we
    # exercise the "promote external_sentiment" branch.
    deduped = _dedup([first, duplicate])
    assert len(deduped) == 1
    assert deduped[0].external_sentiment == 0.7


def test_scorer_thin_coverage_dampened() -> None:
    """One bullish item shouldn't peg us at 1.0 — coverage is too thin."""
    items = bullish_items()[:1]
    scorer = NarrativeScorer(sources=[FakeSource(items)], use_cache=False)
    result = scorer.score("FAKE")
    assert result.score < 0.9  # damped because item_count <= 2
    assert result.score > 0.5


def test_blend_composite_neutral_narrative_pulls_score_toward_50() -> None:
    class _Neutral:
        score = 0.5
    blended = blend_composite(80.0, _Neutral(), narrative_weight=0.2)
    # 0.8 * 80 + 0.2 * 50 = 74
    assert abs(blended - 74.0) < 0.01


def test_blend_composite_bullish_narrative_boosts_score() -> None:
    class _Bull:
        score = 0.9
    blended = blend_composite(60.0, _Bull(), narrative_weight=0.2)
    # 0.8 * 60 + 0.2 * 90 = 66
    assert abs(blended - 66.0) < 0.01


def test_blend_composite_none_narrative_returns_pattern() -> None:
    assert blend_composite(73.0, None) == 73.0


def test_explanation_includes_top_headline() -> None:
    scorer = NarrativeScorer(sources=[FakeSource(bullish_items())], use_cache=False)
    result = scorer.score("FAKE")
    # The top item is the one with strongest sentiment — the earnings beat.
    assert "beats earnings" in result.explanation or "contract" in result.explanation
