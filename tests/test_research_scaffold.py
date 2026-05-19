"""Tests for the deep-research scaffold.

These pin the contract the real research implementations must satisfy:
the protocol shape, the default no-op behavior, the conviction gate,
and the top-N candidate selector. Lives here so a future change to the
scaffold's surface area trips a test before it trips a downstream
implementer.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.research import (
    DEFAULT_CONVICTION_THRESHOLD,
    NullResearcher,
    ResearchResult,
    Researcher,
    should_research,
    top_candidates,
)


@dataclass
class _FakeMatch:
    """Minimal stand-in for MatchResult; only effective_score is needed."""
    symbol: str
    effective_score: float


def test_null_researcher_returns_empty_result() -> None:
    r = NullResearcher()
    out = r.research("nvda")
    assert isinstance(out, ResearchResult)
    assert out.ticker == "NVDA"
    assert out.summary == ""
    assert out.confidence == 0.0
    assert out.sources == ()


def test_null_researcher_satisfies_protocol() -> None:
    # runtime_checkable lets us assert structural typing at runtime.
    assert isinstance(NullResearcher(), Researcher)


def test_should_research_uses_default_threshold() -> None:
    assert should_research(DEFAULT_CONVICTION_THRESHOLD) is True
    assert should_research(DEFAULT_CONVICTION_THRESHOLD - 0.1) is False


def test_should_research_respects_custom_threshold() -> None:
    assert should_research(60.0, threshold=50.0) is True
    assert should_research(40.0, threshold=50.0) is False


def test_top_candidates_filters_and_caps() -> None:
    matches = [
        _FakeMatch("A", 85.0),
        _FakeMatch("B", 72.0),
        _FakeMatch("C", 50.0),   # below default threshold, dropped
        _FakeMatch("D", 90.0),
        _FakeMatch("E", 71.0),
    ]
    picks = top_candidates(matches, limit=3)
    assert [m.symbol for m in picks] == ["D", "A", "B"]


def test_top_candidates_empty_pool_returns_empty_list() -> None:
    matches = [_FakeMatch("A", 30.0), _FakeMatch("B", 40.0)]
    assert top_candidates(matches) == []


def test_top_candidates_unique_by_keeps_highest_per_key() -> None:
    # A single ticker hitting multiple patterns must only consume one
    # research slot. Dedup keeps the highest-scoring entry per symbol.
    matches = [
        _FakeMatch("A", 80.0),
        _FakeMatch("B", 75.0),
        _FakeMatch("A", 90.0),  # higher-scoring A — should win the slot
        _FakeMatch("C", 72.0),
        _FakeMatch("B", 78.0),  # higher-scoring B
    ]
    picks = top_candidates(matches, limit=3, unique_by="symbol")
    assert [(m.symbol, m.effective_score) for m in picks] == [
        ("A", 90.0), ("B", 78.0), ("C", 72.0),
    ]


def test_top_candidates_unique_by_caps_at_limit_not_match_count() -> None:
    # 4 unique symbols above threshold but limit=2 — exactly 2 returned.
    matches = [
        _FakeMatch("A", 90.0),
        _FakeMatch("A", 88.0),  # duplicate, dropped
        _FakeMatch("B", 85.0),
        _FakeMatch("C", 80.0),
        _FakeMatch("D", 75.0),
    ]
    picks = top_candidates(matches, limit=2, unique_by="symbol")
    assert [m.symbol for m in picks] == ["A", "B"]


def test_research_result_to_row_exposes_all_slots() -> None:
    res = ResearchResult(
        ticker="PLTR",
        as_of=NullResearcher().research("PLTR").as_of,
        summary="dominant in defense-AI niche",
        company_quality="profitable, growing",
        management="founder-led, long-tenured",
        partnerships="multiple DoD contracts",
        financial_health="net cash, FCF positive",
        key_risks="customer concentration",
        sources=("10-K", "earnings call"),
        confidence=0.7,
    )
    row = res.to_row()
    expected_keys = {
        "research_summary",
        "research_company_quality",
        "research_management",
        "research_partnerships",
        "research_financial_health",
        "research_key_risks",
        "research_sources",
        "research_confidence",
    }
    assert expected_keys.issubset(row.keys())
    assert row["research_confidence"] == 0.7
    assert row["research_sources"] == "10-K, earnings call"
