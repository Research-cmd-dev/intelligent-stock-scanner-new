"""Tests for the LLM-backed deep-research implementation.

Fully offline. Uses a fake Anthropic client + fake news source so the
suite stays fast and runs without ANTHROPIC_API_KEY. The tests pin:

- happy path returns a populated ResearchResult and threads sources
  through from the headlines basket
- the request shape we send matches the prompt-caching contract (cached
  system block + ticker/headlines in the user turn + JSON schema)
- the headlines cap is enforced and freshness sorting holds
- any failure mode (API error, empty content, malformed JSON, missing
  required field, out-of-range confidence) degrades to an empty
  ResearchResult with confidence=0.0 — never raises
- the class satisfies the Researcher protocol
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pytest

from src.research import (
    DEFAULT_MAX_HEADLINES,
    DEFAULT_MODEL,
    LLMResearcher,
    ResearchResult,
    Researcher,
)
from src.research.llm_researcher import (
    SYSTEM_PROMPT,
    _coerce_confidence,
    _project_headlines,
    _render_user_message,
)


# --------------------------------------------------------------------- #
# Fakes                                                                 #
# --------------------------------------------------------------------- #


@dataclass
class _FakeNewsItem:
    """Mirrors the shape NewsItem exposes — title, summary, url,
    published_utc, provider, publisher, tickers, external_sentiment, raw.
    Only the fields the researcher reads matter here."""

    title: str
    summary: str
    url: str
    published_utc: datetime
    provider: str
    publisher: str
    tickers: tuple[str, ...] = ()
    external_sentiment: float | None = None


class _FakeSource:
    name = "fake"

    def __init__(self, items: list[_FakeNewsItem]) -> None:
        self._items = items
        self.calls: list[tuple[str, int]] = []

    def fetch(self, symbol: str, *, limit: int = 20) -> list[_FakeNewsItem]:
        self.calls.append((symbol, limit))
        return list(self._items[:limit])


class _ExplodingSource:
    name = "exploding"

    def fetch(self, symbol: str, *, limit: int = 20):  # noqa: D401
        raise RuntimeError("simulated upstream failure")


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeResponse:
    content: list[_FakeTextBlock]


class _FakeMessages:
    """Captures call kwargs and returns a canned response."""

    def __init__(self, response: object | Exception) -> None:
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response: object | Exception) -> None:
        self.messages = _FakeMessages(response)
        self.with_options_calls: list[dict] = []

    def with_options(self, **kwargs):
        self.with_options_calls.append(kwargs)
        return self  # same client; lets us assert on .messages.calls


def _payload_response(payload: dict[str, object]) -> _FakeResponse:
    return _FakeResponse(content=[_FakeTextBlock(text=json.dumps(payload))])


def _good_payload() -> dict[str, object]:
    return {
        "summary": "NVDA designs the dominant accelerators for AI training, and capex revisions for FY26 reinforce the demand backdrop.",
        "company_quality": "Wide moat anchored in CUDA, supplier lock-in via custom packaging, and an annual cadence that competitors have not matched. Gross margins in the mid-70s.",
        "management": "Founder-CEO Jensen Huang since 1993, exceptional capital allocator with consistent execution on product roadmap.",
        "partnerships": "Anchor demand from every US hyperscaler plus sovereign AI deals; TSMC and SK Hynix on the supply side, with concentration risk on both.",
        "financial_health": "Net cash, large FCF generation, low capex intensity relative to revenue.",
        "key_risks": "Hyperscaler concentration, China export-control overhang, valuation pricing in continued execution.",
        "confidence": 0.85,
    }


def _good_headlines(today: datetime) -> list[_FakeNewsItem]:
    return [
        _FakeNewsItem(
            title="Nvidia signs multi-year supply pact with sovereign AI fund",
            summary="",
            url="https://example.com/nvda-1",
            published_utc=today - timedelta(days=1),
            provider="polygon",
            publisher="Reuters",
        ),
        _FakeNewsItem(
            title="Hyperscaler capex revised higher for FY26",
            summary="",
            url="https://example.com/nvda-2",
            published_utc=today - timedelta(days=3),
            provider="yfinance",
            publisher="Bloomberg",
        ),
    ]


# --------------------------------------------------------------------- #
# Protocol conformance + scaffold sanity                                #
# --------------------------------------------------------------------- #


def test_llm_researcher_satisfies_researcher_protocol() -> None:
    client = _FakeAnthropicClient(_payload_response(_good_payload()))
    r = LLMResearcher(client=client, sources=[])
    assert isinstance(r, Researcher)
    assert r.name == "llm"


def test_default_model_is_sonnet_4_6() -> None:
    # Locks in the design decision: fast structured synthesis = Sonnet.
    # If anyone bumps to Opus or Haiku, this test forces them to also
    # update the rationale comment in llm_researcher.py.
    assert DEFAULT_MODEL == "claude-sonnet-4-6"


# --------------------------------------------------------------------- #
# Happy path                                                            #
# --------------------------------------------------------------------- #


def test_research_happy_path_populates_result() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    source = _FakeSource(_good_headlines(today))
    client = _FakeAnthropicClient(_payload_response(_good_payload()))
    r = LLMResearcher(client=client, sources=[source], max_headlines=5)

    result = r.research("nvda")

    assert isinstance(result, ResearchResult)
    assert result.ticker == "NVDA"  # upper-cased
    assert result.confidence == pytest.approx(0.85)
    assert "CUDA" in result.company_quality
    assert "Jensen Huang" in result.management
    assert "hyperscaler" in result.key_risks.lower()
    # Sources come from the headlines basket, not the LLM payload.
    assert set(result.sources) == {
        "https://example.com/nvda-1",
        "https://example.com/nvda-2",
    }
    # raw carries the model so downstream can audit which model produced
    # the read without changing the dataclass shape.
    assert result.raw == {"model": DEFAULT_MODEL}


def test_request_shape_matches_prompt_caching_contract() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    source = _FakeSource(_good_headlines(today))
    client = _FakeAnthropicClient(_payload_response(_good_payload()))
    r = LLMResearcher(client=client, sources=[source])

    r.research("NVDA")

    assert len(client.messages.calls) == 1
    call = client.messages.calls[0]

    # Model + max_tokens come from the defaults.
    assert call["model"] == DEFAULT_MODEL
    assert call["max_tokens"] >= 512  # generous headroom for 6 fields + confidence

    # System block is the FROZEN prompt + cache_control. The system
    # prompt is intentionally large; we don't pin the exact text here
    # (it will evolve) but we DO pin that (a) it's the module constant
    # and (b) it's marked for caching. If either of these drifts, the
    # prompt cache silently stops paying off.
    system = call["system"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["text"] is SYSTEM_PROMPT
    assert system[0]["cache_control"] == {"type": "ephemeral"}

    # User turn carries the volatile bits — anything per-request must
    # live here, not in the cached system prefix.
    user_text = call["messages"][0]["content"]
    assert "NVDA" in user_text
    assert "2026-05-19" in user_text
    assert "supply pact with sovereign AI fund" in user_text

    # JSON-schema output forces a parseable response shape.
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["schema"]["required"]) == {
        "summary", "company_quality", "management", "partnerships",
        "financial_health", "key_risks", "confidence",
    }

    # with_options(timeout=...) is used so the per-call timeout doesn't
    # mutate the shared client.
    assert client.with_options_calls and "timeout" in client.with_options_calls[0]


# --------------------------------------------------------------------- #
# Headlines pipeline                                                    #
# --------------------------------------------------------------------- #


def test_max_headlines_caps_basket_size_and_orders_by_freshness() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    items = [
        _FakeNewsItem(
            title=f"Story {i}",
            summary="",
            url=f"https://example.com/{i}",
            # i=0 newest, i=9 oldest
            published_utc=today - timedelta(days=i),
            provider="polygon",
            publisher="Reuters",
        )
        for i in range(10)
    ]
    source = _FakeSource(items)
    client = _FakeAnthropicClient(_payload_response(_good_payload()))
    r = LLMResearcher(client=client, sources=[source], max_headlines=3)

    result = r.research("XYZQ")

    # Result.sources only contains the 3 freshest URLs.
    assert result.sources == (
        "https://example.com/0",
        "https://example.com/1",
        "https://example.com/2",
    )
    # User turn lists exactly 3 stories, freshest first.
    user_text = client.messages.calls[0]["messages"][0]["content"]
    assert "Story 0" in user_text and "Story 1" in user_text and "Story 2" in user_text
    assert "Story 3" not in user_text
    assert user_text.index("Story 0") < user_text.index("Story 2")


def test_empty_headlines_basket_still_calls_claude_with_explicit_marker() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    client = _FakeAnthropicClient(_payload_response(_good_payload()))
    r = LLMResearcher(client=client, sources=[_FakeSource([])])

    result = r.research("XYZQ")

    assert len(client.messages.calls) == 1
    user_text = client.messages.calls[0]["messages"][0]["content"]
    # The renderer flags an empty basket so the model knows to
    # downweight confidence and not hallucinate a catalyst.
    assert "no recent headlines" in user_text.lower()
    # Result is whatever the LLM said — empty basket doesn't force
    # confidence=0 (that's the LLM's call, per the rubric).
    assert result.confidence == pytest.approx(0.85)


def test_news_source_failure_does_not_abort_research() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    good = _FakeSource(_good_headlines(today))
    client = _FakeAnthropicClient(_payload_response(_good_payload()))
    r = LLMResearcher(
        client=client, sources=[_ExplodingSource(), good], max_headlines=5
    )

    result = r.research("NVDA")

    # Good source still contributed; LLM still got called.
    assert len(client.messages.calls) == 1
    assert len(result.sources) == 2
    assert result.confidence == pytest.approx(0.85)


# --------------------------------------------------------------------- #
# Graceful-failure contract                                             #
# --------------------------------------------------------------------- #


def test_api_error_returns_empty_result_with_sources_preserved() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    source = _FakeSource(_good_headlines(today))
    client = _FakeAnthropicClient(RuntimeError("502 Bad Gateway"))
    r = LLMResearcher(client=client, sources=[source])

    result = r.research("NVDA")

    assert result.ticker == "NVDA"
    assert result.confidence == 0.0
    assert result.summary == ""
    assert result.company_quality == ""
    # Sources still come back so the dashboard can show "we tried these
    # headlines" even when the LLM call failed.
    assert len(result.sources) == 2


def test_malformed_json_returns_empty_result() -> None:
    client = _FakeAnthropicClient(
        _FakeResponse(content=[_FakeTextBlock(text="not json at all")])
    )
    r = LLMResearcher(client=client, sources=[])

    result = r.research("XYZQ")

    assert result.confidence == 0.0
    assert result.summary == ""


def test_missing_text_block_returns_empty_result() -> None:
    # Response with no text-typed block (e.g. all thinking/tool blocks).
    client = _FakeAnthropicClient(_FakeResponse(content=[]))
    r = LLMResearcher(client=client, sources=[])

    result = r.research("XYZQ")

    assert result.confidence == 0.0


def test_partial_payload_falls_back_per_field() -> None:
    # The mapper uses .get(..., "") per field, so a payload missing
    # individual slots maps to empty strings — not a hard failure.
    client = _FakeAnthropicClient(_payload_response({
        "summary": "Quick read.",
        "company_quality": "OK.",
        "confidence": 0.4,
    }))
    r = LLMResearcher(client=client, sources=[])

    result = r.research("XYZQ")

    assert result.summary == "Quick read."
    assert result.management == ""  # missing → empty, not crashed
    assert result.confidence == pytest.approx(0.4)


def test_confidence_is_clamped_to_unit_interval() -> None:
    assert _coerce_confidence(1.7) == 1.0
    assert _coerce_confidence(-0.2) == 0.0
    assert _coerce_confidence("not a number") == 0.0
    assert _coerce_confidence(None) == 0.0
    assert _coerce_confidence(0.5) == 0.5


# --------------------------------------------------------------------- #
# Helper-level coverage                                                 #
# --------------------------------------------------------------------- #


def test_project_headlines_handles_naive_datetimes() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    items = [
        _FakeNewsItem(
            title="Naive",
            summary="",
            url="https://example.com/n",
            # naive datetime — should be coerced to UTC, not crash
            published_utc=datetime(2026, 5, 17),
            provider="polygon",
            publisher="Reuters",
        ),
    ]
    out = _project_headlines(items, now=today, limit=5)
    assert len(out) == 1
    assert out[0].age_days == 2


def test_project_headlines_skips_empty_titles() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    items = [
        _FakeNewsItem(
            title="",
            summary="",
            url="https://example.com/empty",
            published_utc=today,
            provider="polygon",
            publisher="Reuters",
        ),
        _FakeNewsItem(
            title="Real",
            summary="",
            url="https://example.com/real",
            published_utc=today,
            provider="polygon",
            publisher="Reuters",
        ),
    ]
    out = _project_headlines(items, now=today, limit=5)
    assert [h.title for h in out] == ["Real"]


def test_render_user_message_formats_date_and_age() -> None:
    today = datetime(2026, 5, 19, tzinfo=timezone.utc)
    headlines = _project_headlines(_good_headlines(today), now=today, limit=5)
    msg = _render_user_message(ticker="NVDA", headlines=headlines, today=today)
    assert "Ticker: NVDA" in msg
    assert "Today's date: 2026-05-19" in msg
    assert "1d ago" in msg
    assert "3d ago" in msg


def test_default_max_headlines_is_sensible() -> None:
    # If this changes, also update the rationale comment in
    # llm_researcher.py — the cap matters for prompt size and cost.
    assert 5 <= DEFAULT_MAX_HEADLINES <= 30
