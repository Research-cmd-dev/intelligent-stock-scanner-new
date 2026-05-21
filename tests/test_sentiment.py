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


# ---------------------------------------------------------------------- #
# Task 3: lexicon false-positive fixes (acceptance cases)
# ---------------------------------------------------------------------- #


def test_raises_debt_is_neutral_not_negative() -> None:
    """'debt' and bare 'raises' removed; headline should be ~0 (not dragged negative)."""
    s = LexiconSentiment()
    score = s.score_item(_make("Apple raises $5B in debt offering"))
    assert -0.2 < score < 0.2, f"expected near-zero polarity, got {score}"


def test_cuts_costs_is_neutral_not_negative() -> None:
    """'cut'/'cuts' removed from NEGATIVE_WORDS (still negative via phrase when appropriate)."""
    s = LexiconSentiment()
    score = s.score_item(_make("GE cuts costs by 12% in Q3"))
    assert -0.2 < score < 0.2, f"expected near-zero polarity, got {score}"


def test_raised_guidance_is_positive_via_phrase() -> None:
    """'raised' word removed from positives, but phrase 'raised guidance' makes it positive."""
    s = LexiconSentiment()
    score = s.score_item(_make("Nvidia raised guidance on AI demand"))
    assert score > 0.1, f"expected positive polarity from phrase, got {score}"


def test_profit_warning_is_negative() -> None:
    """'profit warning' phrase (and 'warning' word) correctly negative."""
    s = LexiconSentiment()
    score = s.score_item(_make("Boeing issued a profit warning"))
    assert score < -0.1, f"expected negative polarity, got {score}"


def test_word_multiplicity_increases_score() -> None:
    """After linear (non-set) token counting, repeated emphasis counts more.

    Using a mixed headline so the ratio (pos-neg)/(pos+neg) actually differs
    with multiplicity (pure-positive cases saturate at +1.0 either way).
    """
    s = LexiconSentiment()
    once = s.score_item(_make("Stock rallies but faces some weakness"))
    thrice = s.score_item(_make("Stock rallies rallies rallies but faces some weakness"))
    assert thrice > once, "repeated positive word should increase polarity"
    assert once >= 0 and thrice > 0.2
