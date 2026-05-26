"""Per-pick verification of speculative picks via Sonnet + web_search.

Takes the aggregator's ``speculative_picks`` list and runs one Sonnet
call per pick with the Anthropic server-side ``web_search`` tool. The
model verifies the ticker exists, looks up the current market cap,
checks whether the actual business matches the thesis, and reports
any recent corroborating or contradicting news.

The returned picks are the originals with verification fields merged
in: ``verified``, ``actual_mcap_billions``, ``actual_business_summary``,
``thesis_matches_business``, ``bucket`` (``"low"`` / ``"mid"`` /
``"large"`` / ``"unknown"``), ``recent_news_supporting``,
``recent_news_contradicting``, ``verification_notes``.

Failures (API error, malformed JSON, missing text block) degrade to a
pick with ``verified=False`` and a populated ``verification_notes`` —
never raises, so a single bad verification can't abort the briefing.

Phase 3.7.3: picks are verified in parallel via :class:`ThreadPoolExecutor`.
The work is I/O-bound (Sonnet + web_search round-trips), so the GIL
doesn't matter; the Anthropic SDK client is thread-safe and shares its
connection pool across threads. Output order matches input order; per-
pick exceptions and a wall-clock deadline degrade individual picks to
``verified=False`` without aborting the batch.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


log = logging.getLogger(__name__)


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2048

# Per-pick wall-clock budget. Sonnet + 1-3 server-side web_search round
# trips usually come back in 20-40s; 90s leaves headroom for slow searches
# without making the batch wait on a single straggler.
PER_PICK_TIMEOUT_S = 90.0

# Cap concurrent Sonnet + web_search calls. Anthropic clients are thread
# safe; we cap to avoid hammering rate limits without buying much wall
# clock improvement beyond the slowest pick.
MAX_PARALLEL_PICKS = 5

# SDK-level HTTP timeout per call. The SDK retries within this budget.
_API_CALL_TIMEOUT_S = 60.0

# Anthropic's server-side web search tool. The model decides when to
# call it; Anthropic executes it server-side and returns the results
# inline in the response content blocks.
_WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}


# --------------------------------------------------------------------- #
# Schema                                                                #
# --------------------------------------------------------------------- #


class _Verification(BaseModel):
    """Shape Sonnet must emit (as JSON) after its web search."""

    verified: bool
    actual_mcap_billions: float | None = None
    actual_business_summary: str = ""
    thesis_matches_business: bool = False
    bucket: str = "unknown"
    recent_news_supporting: str | None = None
    recent_news_contradicting: str | None = None
    verification_notes: str = ""

    @field_validator("bucket")
    @classmethod
    def _validate_bucket(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in {"low", "mid", "large", "unknown"} else "unknown"


# --------------------------------------------------------------------- #
# Prompt                                                                #
# --------------------------------------------------------------------- #


_PROMPT_TEMPLATE = """You are verifying a speculative stock-market research pick.

Use the web_search tool to confirm the pick below. Search for the company,
its current market cap, and any recent news (last 30 days) that supports
or contradicts the thesis.

PICK TO VERIFY:
- Ticker: {ticker}
- Name: {name}
- Target bucket (aggregator's intent): {target_bucket}   (low = $300M-$2B, mid = $2B-$10B)
- Aggregator's mcap estimate: {est_mcap}B
- Amplified by: {amplified_by}
- Thesis: {thesis}

VERIFY:
1. Does this ticker exist on a US exchange (NYSE / NASDAQ / AMEX)? If it's foreign, an ETF, OTC, or not real -> verified=false.
2. What is the current market cap, in billions of USD?
3. Does the company's actual business plausibly match the thesis above?
4. Is there any recent news (last 30 days) that supports or contradicts the thesis?

CLASSIFICATION:
- bucket = "low" if actual market cap is $300M-$2B
- bucket = "mid" if actual market cap is $2B-$10B
- bucket = "large" if actual market cap is greater than $10B
- bucket = "unknown" if you cannot determine the market cap

OUTPUT FORMAT:
Return ONLY a single JSON object with no prose, no markdown fences, no preamble.

{{
  "verified": true | false,
  "actual_mcap_billions": <number or null>,
  "actual_business_summary": "<one sentence describing what the company does>",
  "thesis_matches_business": true | false,
  "bucket": "low" | "mid" | "large" | "unknown",
  "recent_news_supporting": "<short snippet or null>",
  "recent_news_contradicting": "<short snippet or null>",
  "verification_notes": "<brief one-line note on how confident you are>"
}}
"""


# --------------------------------------------------------------------- #
# Public API                                                            #
# --------------------------------------------------------------------- #


def research_picks(
    *,
    picks: list[dict[str, Any]],
    client: Any,
    model: str = DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    """Verify each pick via web search. Returns enriched picks (same order).

    Picks are verified concurrently via :class:`ThreadPoolExecutor` bounded
    by :data:`MAX_PARALLEL_PICKS`. Output order matches input order — a
    later-submitted pick completing first does not jump position. Per-pick
    exceptions and the per-pick wall-clock deadline degrade individual
    picks to ``verified=False`` with a populated ``verification_notes``
    rather than aborting the batch.
    """
    if not picks:
        return picks

    overall_start = time.monotonic()
    log.info(
        "research_picks: verifying %d picks (max_workers=%d)",
        len(picks),
        MAX_PARALLEL_PICKS,
    )

    results: list[dict[str, Any] | None] = [None] * len(picks)

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_PICKS) as executor:
        future_to_idx = {
            executor.submit(_verify_with_safety, pick, client, model): i
            for i, pick in enumerate(picks)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            ticker = picks[idx].get("ticker", "?")
            try:
                results[idx] = future.result()
            except Exception as exc:
                # _verify_with_safety already swallows; this is belt-and-suspenders.
                log.warning("pick %s executor-error: %s", ticker, exc)
                results[idx] = {**picks[idx], **_failed(f"executor: {exc}")}

    final: list[dict[str, Any]] = [
        r if r is not None else {**picks[i], **_failed("no_result")}
        for i, r in enumerate(results)
    ]

    elapsed = time.monotonic() - overall_start
    verified = sum(1 for r in final if r.get("verified"))
    log.info(
        "research_picks done: %d/%d verified in %.1fs",
        verified, len(final), elapsed,
    )
    return final


# --------------------------------------------------------------------- #
# Internals                                                             #
# --------------------------------------------------------------------- #


def _verify_with_safety(
    pick: dict[str, Any], client: Any, model: str
) -> dict[str, Any]:
    """Run one pick verification with timing log + exception isolation.

    Always returns a pick-shaped dict (original fields merged with
    verification fields). Never raises — TimeoutError and any other
    exception become ``verified=False`` with the failure in
    ``verification_notes``.
    """
    start = time.monotonic()
    ticker = pick.get("ticker", "?")
    try:
        verification = _verify_single_pick(
            pick=pick,
            client=client,
            model=model,
            deadline=start + PER_PICK_TIMEOUT_S,
        )
        log.info("verified %s in %.1fs", ticker, time.monotonic() - start)
        return {**pick, **verification}
    except TimeoutError as exc:
        log.warning(
            "pick %s timed out after %.1fs: %s",
            ticker, time.monotonic() - start, exc,
        )
        return {**pick, **_failed(f"per_pick_timeout: {exc}")}
    except Exception as exc:
        log.warning(
            "pick %s failed after %.1fs: %s",
            ticker, time.monotonic() - start, exc,
        )
        return {**pick, **_failed(f"verification raised: {exc}")}


def _verify_single_pick(
    *,
    pick: dict[str, Any],
    client: Any,
    model: str,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Single Sonnet + web_search call. Raises ``TimeoutError`` past deadline."""
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("deadline exceeded before API call")

    prompt = _build_prompt(pick)
    resp = client.messages.create(
        model=model,
        max_tokens=DEFAULT_MAX_TOKENS,
        tools=[_WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": prompt}],
        timeout=_API_CALL_TIMEOUT_S,
    )

    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("deadline exceeded after API call")

    text = _extract_final_text(resp)
    return _parse_verification(text)


def _build_prompt(pick: dict[str, Any]) -> str:
    return _PROMPT_TEMPLATE.format(
        ticker=pick.get("ticker", "?"),
        name=pick.get("name", "(unknown)"),
        target_bucket=pick.get("target_bucket", "unknown"),
        est_mcap=pick.get("estimated_mcap_billions"),
        amplified_by=pick.get("tickers_amplified_by") or [],
        thesis=pick.get("thesis", ""),
    )


def _extract_final_text(resp: Any) -> str:
    """Return the LAST text block in the response.

    With the server-side web_search tool, the response content interleaves
    ``server_tool_use`` blocks, ``web_search_tool_result`` blocks, and
    ``text`` blocks. The model's final JSON answer is the last text block.
    """
    content = getattr(resp, "content", None) or []
    texts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            t = getattr(block, "text", "") or ""
            if t:
                texts.append(t)
    if not texts:
        raise ValueError("no text block in verification response")
    return texts[-1]


def _parse_verification(text: str) -> dict[str, Any]:
    """Strip fences, find JSON, validate against the schema.

    Sonnet sometimes wraps JSON in code fences or trailing commentary
    even when told not to. We tolerate that by extracting the first
    ``{...}`` blob and parsing it.
    """
    cleaned = _strip_fences(text).strip()
    data = _try_parse_json(cleaned)
    if data is None:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m is not None:
            data = _try_parse_json(m.group(0))
    if data is None:
        return _failed("unparseable verification JSON")
    try:
        return _Verification.model_validate(data).model_dump()
    except ValidationError as exc:
        return _failed(f"validation failed: {exc.errors()[0]['msg']}")


def _try_parse_json(s: str) -> dict[str, Any] | None:
    try:
        out = json.loads(s)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def _failed(reason: str) -> dict[str, Any]:
    return _Verification(
        verified=False,
        actual_mcap_billions=None,
        actual_business_summary="",
        thesis_matches_business=False,
        bucket="unknown",
        recent_news_supporting=None,
        recent_news_contradicting=None,
        verification_notes=reason,
    ).model_dump()
