"""Tests for the lexicon sentiment engine."""

from __future__ import annotations

from datetime import datetime, timezone

from src.narrative.sentiment import LexiconSentiment
from src.narrative.sources.base import NewsItem


def _make(title: str, *, summary: str = "", ext: float | None = None) -> NewsItem:
    return NewsItem(
        title=title, summary=summary, url="http://x",
        published_utc=datetime.now(tz=timezone.utc),
        provider="test", publisher="test",
        external_sentiment=ext,
    )


def test_positive_words_score_positive() -> None:
    s = LexiconSentiment()
    assert s.score_item(_make("Company beats estimates and raises guidance")) > 0


def test_negative_words_score_negative() -> None:
    s = LexiconSentiment()
    assert s.score_item(_make("Company misses estimates and cuts guidance")) < 0


def test_no_lexicon_hits_returns_neutral() -> None:
    s = LexiconSentiment()
    assert s.score_item(_make("Routine board meeting was held today")) == 0.0


def test_phrase_match_counts() -> None:
    s = LexiconSentiment()
    score = s.score_item(_make("Stock hits all time high on strong demand"))
    assert score > 0


def test_external_sentiment_preferred_when_set() -> None:
    s = LexiconSentiment(prefer_external=True)
    # Lexicon would say neutral; external says strongly negative.
    item = _make("Routine meeting", ext=-0.7)
    assert s.score_item(item) == -0.7


def test_external_sentiment_ignored_when_disabled() -> None:
    s = LexiconSentiment(prefer_external=False)
    item = _make("Company beats estimates", ext=-0.7)
    # Should now ignore external and use lexicon (positive).
    assert s.score_item(item) > 0


def test_mixed_text_balances_out() -> None:
    s = LexiconSentiment()
    score = s.score_item(_make("Company beat estimates but missed on revenue"))
    # One positive, one negative -> ~0
    assert -0.5 < score < 0.5
