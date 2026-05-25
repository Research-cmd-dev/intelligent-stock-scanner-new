"""Daily cross-episode aggregator: one Claude Sonnet call.

The deterministic pieces (ticker rollup, theme novelty) come from the
sibling :mod:`ticker_aggregator` and :mod:`novelty_detector` modules.
This module is the *judgment* layer — Sonnet receives the per-episode
summaries and a short digest of the last 14 days' themes/tickers, and
returns the headline, themes_today list, notable_firsts, and any
cross-episode observations it spots.

Same JSON-validation-with-retry pattern as the summarizer.
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .novelty_detector import emerging_themes
from .ticker_aggregator import rollup_tickers


log = logging.getLogger(__name__)


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2048


# --------------------------------------------------------------------- #
# Pydantic schema (LLM-produced subset only)                            #
# --------------------------------------------------------------------- #


class _LLMOutput(BaseModel):
    """Shape Sonnet is asked to emit. Rollup + emerging_themes are
    computed deterministically and not asked of the LLM."""

    headline: str
    themes_today: list[str] = Field(default_factory=list)
    notable_firsts: list[str] = Field(default_factory=list)
    cross_episode_observations: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------- #
# Prompt                                                                #
# --------------------------------------------------------------------- #


_INSTRUCTIONS = """You are the daily editor for a stock-market intelligence briefing.

You receive:
1. A list of per-episode summaries (JSON) that were ingested in the last 24 hours.
2. A short digest of the last 14 days' briefings (themes seen, tickers most discussed).
3. The ticker rollup for today (already computed — you do NOT need to recompute it).

YOUR TASK:
Return a JSON object with these exact fields:
- "headline": one-sentence framing of the day's most important narrative thread. Concrete, specific, no marketing language. Reference a named speaker or ticker when possible.
- "themes_today": 4-10 high-level themes that surface across today's episodes. Use noun phrases.
- "notable_firsts": observations that are notably first-time: a speaker addressing a topic they hadn't before, a framing not in the 14-day digest, a ticker entering the conversation. Each item is one sentence.
- "cross_episode_observations": resonances or contradictions between episodes — two speakers using the same framing, two episodes contradicting each other on the same ticker. Each item is one sentence.

RULES:
- Be specific. "Markets discussed" is filler. "Gerstner and Powell both used 'data-dependent' framing in the same 24h" is signal.
- Don't fabricate. If a theme isn't actually in the episode summaries, don't include it.
- Output ONLY valid JSON. No markdown fences. No prose around the JSON.
"""


_RETRY_PREAMBLE = (
    "Your previous output failed schema validation. Re-output the JSON with no commentary "
    "and no markdown fences.\n\n"
)


# --------------------------------------------------------------------- #
# Public API                                                            #
# --------------------------------------------------------------------- #


def aggregate_daily(
    *,
    briefing_date: date_,
    episode_summaries: list[dict[str, Any]],
    recent_briefings: list[dict[str, Any]],
    client: Any,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Run the daily aggregation. Returns the full DailyAggregation dict."""
    ticker_rollup = rollup_tickers(episode_summaries)
    digest = _digest_recent(recent_briefings)

    payload = _render_prompt(
        briefing_date=briefing_date,
        episode_summaries=episode_summaries,
        digest=digest,
        ticker_rollup=ticker_rollup,
    )
    llm_out = _call_with_retry(client=client, model=model, prompt=payload)

    emerging = emerging_themes(llm_out["themes_today"], recent_briefings)

    return {
        "headline": llm_out["headline"],
        "ticker_rollup": ticker_rollup,
        "themes_today": llm_out["themes_today"],
        "emerging_themes": emerging,
        "notable_firsts": llm_out["notable_firsts"],
        "cross_episode_observations": llm_out["cross_episode_observations"],
    }


# --------------------------------------------------------------------- #
# Prompt assembly                                                       #
# --------------------------------------------------------------------- #


def _render_prompt(
    *,
    briefing_date: date_,
    episode_summaries: list[dict[str, Any]],
    digest: dict[str, Any],
    ticker_rollup: list[dict[str, Any]],
) -> str:
    body = {
        "briefing_date": briefing_date.isoformat(),
        "episode_summaries": episode_summaries,
        "ticker_rollup_today": ticker_rollup,
        "last_14_days_digest": digest,
    }
    return (
        f"{_INSTRUCTIONS}\n\n"
        f"INPUT:\n{json.dumps(body, indent=2, default=str)}\n\n"
        f"JSON:\n"
    )


def _digest_recent(recent_briefings: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact ``{themes, tickers}`` summary of the last 14 days."""
    themes: set[str] = set()
    tickers: dict[str, int] = {}
    for row in recent_briefings:
        raw = row.get("structured_json")
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        agg = (data or {}).get("aggregation") or {}
        for t in agg.get("themes_today") or []:
            if isinstance(t, str):
                themes.add(t.strip())
        for r in agg.get("ticker_rollup") or []:
            sym = (r.get("symbol") or "").upper()
            if sym:
                tickers[sym] = tickers.get(sym, 0) + int(r.get("total_mentions") or 0)
    top = sorted(tickers.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    return {
        "themes_seen": sorted(themes),
        "top_tickers": [{"symbol": k, "total_mentions": v} for k, v in top],
    }


# --------------------------------------------------------------------- #
# Call + parse                                                          #
# --------------------------------------------------------------------- #


def _call_with_retry(*, client: Any, model: str, prompt: str) -> dict[str, Any]:
    for attempt in (0, 1):
        active = prompt if attempt == 0 else f"{_RETRY_PREAMBLE}{prompt}"
        try:
            raw = _invoke(client, model, active)
        except Exception as exc:
            log.warning("aggregator LLM call raised (attempt %d): %s", attempt, exc)
            continue
        parsed = _try_parse(raw)
        if parsed is not None:
            return parsed
        log.warning("aggregator validation failed (attempt %d)", attempt)
    return _stub_aggregation()


def _invoke(client: Any, model: str, prompt: str) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=DEFAULT_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    content = getattr(resp, "content", None) or []
    for block in content:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    raise ValueError("no text block in aggregator response")


def _try_parse(raw: str) -> dict[str, Any] | None:
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        return _LLMOutput.model_validate(data).model_dump()
    except ValidationError:
        return None


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


def _stub_aggregation() -> dict[str, Any]:
    return _LLMOutput(
        headline="(LLM aggregation failed; see episode summaries below)",
        themes_today=[],
        notable_firsts=[],
        cross_episode_observations=[],
    ).model_dump()
