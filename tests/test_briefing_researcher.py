"""Tests for ``src.narrative.briefing.llm_researcher.research_picks``.

The researcher runs one Sonnet call per pick with the server-side
``web_search`` tool. Responses interleave ``server_tool_use`` blocks,
``web_search_tool_result`` blocks, and ``text`` blocks — the final
JSON answer is the LAST text block.

Phase 3.7.3: picks run in parallel via ThreadPoolExecutor, so the stub
client must be thread-safe. For multi-pick tests where call order is
non-deterministic, the stub routes responses by the ticker mentioned
in the prompt.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable

from src.narrative.briefing import llm_researcher
from src.narrative.briefing.llm_researcher import research_picks


# --------------------------------------------------------------------- #
# Stub client                                                            #
# --------------------------------------------------------------------- #


class _Block:
    """Generic content block — `type` controls how the extractor treats it."""

    def __init__(self, block_type: str, text: str = "") -> None:
        self.type = block_type
        self.text = text


class _Response:
    def __init__(self, blocks: list[_Block]) -> None:
        self.content = blocks


# Type alias: a router is a callable that receives the create() kwargs
# and returns either a _Response or an Exception (to be raised).
_Router = Callable[[dict[str, Any]], Any]


class _Messages:
    """Thread-safe stub.

    Accepts either a list of responses (consumed FIFO — only safe when
    call order is deterministic, i.e. single-pick tests) or a callable
    router that picks a response based on the create() kwargs.
    """

    def __init__(self, responses: list[Any] | _Router) -> None:
        self._responses = responses
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        with self._lock:
            self.calls.append(kwargs)
            if not callable(self._responses):
                out = self._responses.pop(0)
                if isinstance(out, Exception):
                    raise out
                return out
            router = self._responses
        # Callable path: invoke OUTSIDE the lock so slow stubs (sleep)
        # don't serialize parallel callers and defeat the test.
        out = router(kwargs)
        if isinstance(out, Exception):
            raise out
        return out


class _Client:
    def __init__(self, responses: list[Any] | _Router) -> None:
        self.messages = _Messages(responses)


def _text_resp(text: str) -> _Response:
    return _Response([_Block("text", text)])


def _interleaved_resp(final_json: str) -> _Response:
    """Realistic response: a tool_use block, a tool_result block, then the
    final text answer. The extractor must skip past the non-text blocks."""
    return _Response([
        _Block("server_tool_use"),
        _Block("web_search_tool_result"),
        _Block("text", "Let me search for this ticker.\n"),
        _Block("server_tool_use"),
        _Block("web_search_tool_result"),
        _Block("text", final_json),
    ])


def _good_verification(
    *,
    bucket: str = "low",
    mcap: float = 0.4,
    verified: bool = True,
) -> str:
    return json.dumps({
        "verified": verified,
        "actual_mcap_billions": mcap,
        "actual_business_summary": "Last-mile autonomous delivery.",
        "thesis_matches_business": True,
        "bucket": bucket,
        "recent_news_supporting": "Q1 expansion announced.",
        "recent_news_contradicting": None,
        "verification_notes": "high confidence",
    })


def _pick(ticker: str = "SERV", target_bucket: str = "low") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "name": f"{ticker} Inc",
        "estimated_mcap_billions": 0.4,
        "thesis": "Adjacency to physical-AI narrative.",
        "narrative_source": "Jensen on Dwarkesh",
        "tickers_amplified_by": ["NVDA"],
        "conviction": "medium",
        "target_bucket": target_bucket,
    }


def _ticker_from_kwargs(kwargs: dict[str, Any]) -> str:
    """Pull the ticker out of the prompt — needed for ticker-routed mocks."""
    content = kwargs["messages"][0]["content"]
    m = re.search(r"Ticker:\s*(\S+)", content)
    return m.group(1) if m else ""


def _route_by_ticker(fn: Callable[[str], Any]) -> _Router:
    """Wrap a ``ticker -> response`` callable as a kwargs-based router."""
    def router(kwargs: dict[str, Any]) -> Any:
        return fn(_ticker_from_kwargs(kwargs))
    return router


# --------------------------------------------------------------------- #
# Tests                                                                  #
# --------------------------------------------------------------------- #


def test_empty_picks_returns_empty_without_calling_client() -> None:
    client = _Client([])
    out = research_picks(picks=[], client=client)
    assert out == []
    assert client.messages.calls == []


def test_happy_path_merges_verification_into_pick() -> None:
    client = _Client([_text_resp(_good_verification())])
    picks = [_pick("SERV")]

    out = research_picks(picks=picks, client=client)

    assert len(out) == 1
    enriched = out[0]
    # Original pick fields preserved
    assert enriched["ticker"] == "SERV"
    assert enriched["thesis"] == "Adjacency to physical-AI narrative."
    assert enriched["tickers_amplified_by"] == ["NVDA"]
    # Verification fields merged in
    assert enriched["verified"] is True
    assert enriched["actual_mcap_billions"] == 0.4
    assert enriched["bucket"] == "low"
    assert enriched["thesis_matches_business"] is True


def test_extractor_uses_last_text_block_when_interleaved() -> None:
    """Anthropic responses with server-side tool use interleave block types.
    The extractor must pick the LAST text block, not the first."""
    client = _Client([_interleaved_resp(_good_verification(bucket="mid"))])
    out = research_picks(picks=[_pick("EXAS")], client=client)
    assert out[0]["verified"] is True
    assert out[0]["bucket"] == "mid"


def test_fenced_json_is_tolerated() -> None:
    """Sonnet sometimes wraps JSON in ```json fences even when asked not to."""
    fenced = "```json\n" + _good_verification() + "\n```"
    client = _Client([_text_resp(fenced)])
    out = research_picks(picks=[_pick("SERV")], client=client)
    assert out[0]["verified"] is True


def test_json_embedded_in_prose_is_extracted_via_regex() -> None:
    """If the model adds preamble, the regex fallback pulls the JSON blob."""
    body = (
        "Here is my verification:\n\n"
        + _good_verification()
        + "\n\nThat's my answer."
    )
    client = _Client([_text_resp(body)])
    out = research_picks(picks=[_pick("SERV")], client=client)
    assert out[0]["verified"] is True
    assert out[0]["bucket"] == "low"


def test_invalid_bucket_value_coerces_to_unknown() -> None:
    bad = json.dumps({
        "verified": True,
        "actual_mcap_billions": 1.0,
        "actual_business_summary": "Something.",
        "thesis_matches_business": True,
        "bucket": "tiny",
        "verification_notes": "ok",
    })
    client = _Client([_text_resp(bad)])
    out = research_picks(picks=[_pick("SERV")], client=client)
    assert out[0]["bucket"] == "unknown"


def test_unparseable_json_degrades_to_failed_with_notes() -> None:
    client = _Client([_text_resp("the ticker looks fine but I cannot verify")])
    out = research_picks(picks=[_pick("SERV")], client=client)
    enriched = out[0]
    assert enriched["verified"] is False
    assert enriched["bucket"] == "unknown"
    assert "unparseable" in enriched["verification_notes"]
    # Original pick fields still intact
    assert enriched["ticker"] == "SERV"
    assert enriched["thesis"] == "Adjacency to physical-AI narrative."


def test_no_text_block_in_response_degrades_to_failed() -> None:
    """If Anthropic returns only tool blocks (e.g. truncated response),
    the failure surfaces in verification_notes rather than raising."""
    client = _Client([_Response([_Block("server_tool_use")])])
    out = research_picks(picks=[_pick("SERV")], client=client)
    assert out[0]["verified"] is False
    assert "no text block" in out[0]["verification_notes"].lower() or \
           "verification raised" in out[0]["verification_notes"].lower()


def test_client_exception_degrades_to_failed() -> None:
    """A raised API error becomes verified=False — one bad pick never
    aborts the whole briefing."""
    client = _Client([RuntimeError("api down")])
    out = research_picks(picks=[_pick("SERV")], client=client)
    assert out[0]["verified"] is False
    assert "api down" in out[0]["verification_notes"]


def test_failure_for_one_pick_does_not_block_other_picks() -> None:
    """Researcher must keep going past a single failure — and with
    parallel execution, the failing pick's response is routed by ticker
    so we don't rely on call order."""
    def route(ticker: str) -> Any:
        if ticker == "FAILME":
            return RuntimeError("transient")
        return _text_resp(_good_verification(bucket="mid", mcap=4.0))

    client = _Client(_route_by_ticker(route))
    out = research_picks(
        picks=[_pick("FAILME"), _pick("OKPICK", target_bucket="mid")],
        client=client,
    )
    assert len(out) == 2
    # Output order matches input order (regardless of completion order)
    assert out[0]["ticker"] == "FAILME"
    assert out[0]["verified"] is False
    assert out[1]["ticker"] == "OKPICK"
    assert out[1]["verified"] is True
    assert out[1]["bucket"] == "mid"


def test_output_order_matches_input_order() -> None:
    """Order must be preserved so the formatter's bucket logic is deterministic."""
    bucket_by_ticker = {"A": "low", "B": "mid", "C": "large"}
    mcap_by_ticker = {"A": 0.5, "B": 5.0, "C": 50.0}

    def route(ticker: str) -> _Response:
        return _text_resp(_good_verification(
            bucket=bucket_by_ticker[ticker],
            mcap=mcap_by_ticker[ticker],
        ))

    client = _Client(_route_by_ticker(route))
    out = research_picks(
        picks=[_pick("A"), _pick("B"), _pick("C")],
        client=client,
    )
    assert [p["ticker"] for p in out] == ["A", "B", "C"]
    assert [p["bucket"] for p in out] == ["low", "mid", "large"]


def test_web_search_tool_passed_to_client() -> None:
    """The verifier MUST register the web_search tool — without it Sonnet
    can't actually verify anything."""
    client = _Client([_text_resp(_good_verification())])
    research_picks(picks=[_pick("SERV")], client=client)
    call = client.messages.calls[0]
    assert "tools" in call
    tool_names = [t.get("name") for t in call["tools"]]
    assert "web_search" in tool_names


# --------------------------------------------------------------------- #
# Phase 3.7.3 — parallel execution                                      #
# --------------------------------------------------------------------- #


def test_research_picks_runs_in_parallel() -> None:
    """5 picks each simulating 0.5s of API latency should finish in
    ~one pick's worth of wall clock, not 5x. With max_workers=5 and
    a 0.5s sleep per call we expect <1.5s instead of ~2.5s sequential."""
    def slow_route(_ticker: str) -> _Response:
        time.sleep(0.5)
        return _text_resp(_good_verification(bucket="low"))

    client = _Client(_route_by_ticker(slow_route))
    picks = [_pick(f"T{i}") for i in range(5)]

    start = time.monotonic()
    out = research_picks(picks=picks, client=client)
    elapsed = time.monotonic() - start

    assert elapsed < 1.5, f"expected parallel execution (<1.5s), got {elapsed:.2f}s"
    assert len(out) == 5
    assert all(r.get("verified") for r in out)


def test_output_order_preserved_under_skewed_completion() -> None:
    """Parallel completion order is non-deterministic. Even when later
    picks finish first, output order must match input order."""
    def variable_route(ticker: str) -> _Response:
        idx = int(ticker[1:])
        # T0 slowest (0.25s), T4 fastest (0.05s) — so completion order
        # is reverse of input order. Output order must still be 0..4.
        time.sleep(0.05 * (5 - idx))
        return _text_resp(_good_verification(bucket="low", mcap=float(idx)))

    client = _Client(_route_by_ticker(variable_route))
    picks = [_pick(f"T{i}") for i in range(5)]

    out = research_picks(picks=picks, client=client)

    assert [r["ticker"] for r in out] == ["T0", "T1", "T2", "T3", "T4"]
    # Sanity: each ticker received its own mcap response (routing worked).
    assert [r["actual_mcap_billions"] for r in out] == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_per_pick_deadline_marks_only_the_slow_pick_failed(
    monkeypatch: Any,
) -> None:
    """If one pick exceeds the per-pick deadline, only that pick is
    marked verified=False — the rest still verify successfully."""
    # Shrink the deadline for the duration of this test so we don't have
    # to wait 90s for a real timeout to fire.
    monkeypatch.setattr(llm_researcher, "PER_PICK_TIMEOUT_S", 0.2)

    def route(ticker: str) -> _Response:
        if ticker == "TSLOW":
            time.sleep(0.5)  # exceeds 0.2s deadline (checked after call)
        return _text_resp(_good_verification(bucket="low"))

    client = _Client(_route_by_ticker(route))
    picks = [_pick("TFAST1"), _pick("TSLOW"), _pick("TFAST2")]

    start = time.monotonic()
    out = research_picks(picks=picks, client=client)
    elapsed = time.monotonic() - start

    # Total runtime is bounded by TSLOW's 0.5s (not blocked on anything else).
    assert elapsed < 2.0, f"slow pick should not block fast picks; took {elapsed:.2f}s"

    by_ticker = {r["ticker"]: r for r in out}
    assert by_ticker["TFAST1"]["verified"] is True
    assert by_ticker["TFAST2"]["verified"] is True
    # TSLOW failed the post-call deadline check
    assert by_ticker["TSLOW"]["verified"] is False
    notes = by_ticker["TSLOW"]["verification_notes"].lower()
    assert "timeout" in notes or "deadline" in notes
