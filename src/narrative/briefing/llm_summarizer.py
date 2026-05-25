"""Per-episode Claude Haiku summarizer.

Public entry point :func:`summarize_episode` takes one episode row + its
chunks and returns a structured summary dict matching :class:`EpisodeSummary`.

Failure handling:
  * Truncation when total chunk text exceeds ``MAX_INPUT_CHARS`` (~100K):
    score chunks by ticker/speaker mention count then ``chunk_idx``, keep
    the top-scoring chunks until under budget, re-sort by ``chunk_idx``.
  * JSON validation via pydantic. One retry with a stricter "no commentary"
    prompt; on second failure, return a stub summary so the daily briefing
    isn't aborted by a single bad LLM response.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


log = logging.getLogger(__name__)


DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Token budget for the transcript portion of the prompt.
# Haiku context is large; this cap keeps per-episode cost predictable.
MAX_INPUT_CHARS = 100_000

# Cap for fixed prompt overhead + JSON response (rough — Haiku is cheap).
DEFAULT_MAX_TOKENS = 2048


# --------------------------------------------------------------------- #
# Pydantic schemas                                                      #
# --------------------------------------------------------------------- #


class TickerMention(BaseModel):
    symbol: str
    sentiment: float = Field(ge=-1.0, le=1.0, default=0.0)
    mentions: int = Field(ge=0, default=0)
    context: str = ""

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class NotableClaim(BaseModel):
    claim: str
    speaker: str | None = None
    polarity: float = Field(ge=-1.0, le=1.0, default=0.0)


class EpisodeSummary(BaseModel):
    """Per-episode briefing entry. Mirrors the §4 schema."""

    episode_id: str
    title: str
    channel: str
    speakers: list[str] = Field(default_factory=list)
    summary: str
    tickers: list[TickerMention] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    notable_claims: list[NotableClaim] = Field(default_factory=list)
    tone: str = "neutral"
    key_chunks: list[int] = Field(default_factory=list)
    deep_links: list[str] = Field(default_factory=list)

    @field_validator("tone")
    @classmethod
    def _validate_tone(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in {"bullish", "bearish", "mixed", "neutral"} else "neutral"


# --------------------------------------------------------------------- #
# Prompt                                                                #
# --------------------------------------------------------------------- #


_SCHEMA_DOC = """{
  "episode_id": "<copy from metadata>",
  "title": "<copy from metadata>",
  "channel": "<copy from metadata>",
  "speakers": ["<speaker_id>", ...],
  "summary": "Three-sentence summary capturing the core argument and any surprises.",
  "tickers": [
    {"symbol": "NVDA", "sentiment": 0.8, "mentions": 7, "context": "AI factories at 10x scale"}
  ],
  "themes": ["AI capex", "sovereign AI"],
  "notable_claims": [
    {"claim": "AI factories will be 10x larger by 2028", "speaker": "jensen_huang", "polarity": 0.8}
  ],
  "tone": "bullish | bearish | mixed | neutral",
  "key_chunks": [31, 45, 102],
  "deep_links": []
}"""


_BASE_INSTRUCTIONS = """You are analyzing a podcast transcript for a stock-market intelligence briefing.

YOUR TASK:
Extract a structured briefing entry as JSON matching the schema below. Focus on content that has stock-market implications. Ignore tangents (food, travel, banter) unless they reference a watched ticker.

RULES:
- "tickers" list: ONLY include actually-traded public-company stock symbols (NVDA, AAPL, GOOGL, TSM, MSFT, META, etc.). Private companies (OpenAI, Anthropic, xAI, Stripe, SpaceX, Databricks, etc.) MUST NOT appear in `tickers` — mention them in `notable_claims` with the company name in the claim text instead.
- Company names are NOT tickers. Convert: "Apple" -> AAPL, "Microsoft" -> MSFT, "Google"/"Alphabet" -> GOOGL, "TSMC"/"Taiwan Semi" -> TSM, "Meta"/"Facebook" -> META, "Tesla" -> TSLA, "NVIDIA" -> NVDA. Output the ticker symbol, never the company name.
- If unsure whether a company is publicly traded, OMIT from `tickers` and put in `notable_claims` instead. False positives are worse than omissions.
- "sentiment": -1.0 to +1.0. Positive = bullish on the ticker. Negative = bearish. Zero = neutral or mentioned only contextually.
- "mentions": count of distinct chunks (timestamped blocks) that mention the ticker.
- "themes": 2-5 high-level themes discussed. Use noun phrases like "AI capex saturation" or "rate cut timing."
- "notable_claims": specific forward-looking statements with stock-market relevance. Include speaker_id from the ingestion tag when attributable.
- "tone": overall episode tone toward markets/tech. One of: bullish, bearish, mixed, neutral.
- "key_chunks": 3-7 chunk indices (the numbers inside [chunk N | HH:MM:SS] markers) that drove your extraction. Leave "deep_links" empty — it's populated downstream.
- Copy "episode_id", "title", "channel" verbatim from EPISODE METADATA.
- Output ONLY valid JSON. No prose preamble or trailing commentary. No markdown fences.
"""


_RETRY_PREAMBLE = (
    "Your previous output failed schema validation. Re-output the JSON with no commentary, "
    "no markdown fences, and strict adherence to the schema below.\n\n"
)


# --------------------------------------------------------------------- #
# Truncation heuristic                                                  #
# --------------------------------------------------------------------- #


# Plausible public-company tickers: 1-5 uppercase letters with word boundaries.
# Won't catch dotted symbols like MOG.A; that's fine for the truncation heuristic.
_TICKER_HINT = re.compile(r"\b[A-Z]{1,5}\b")

# Words that look like tickers but aren't — common English short-uppercase
# tokens that would otherwise inflate every chunk's score.
_TICKER_FALSE_POSITIVES = frozenset({
    "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP",
    "US", "WE", "AND", "ARE", "BUT", "FOR", "GOT", "HAS", "HAD", "HER",
    "HIS", "HOW", "NOT", "NOW", "OUR", "OUT", "SHE", "THE", "WHO", "WHY",
    "YOU", "ALL", "WAS", "WAY", "TWO", "THIS", "THAT", "WITH", "FROM",
    "JUST", "LIKE", "WHAT", "WHEN", "OK", "OKAY",
    "AI", "CEO", "CTO", "CFO", "IPO", "USD", "EU", "UK", "US", "GDP",
})


def _chunk_score(text: str, speaker_hints: list[str]) -> int:
    """Higher = more likely to contain ticker/speaker content."""
    hits = 0
    for m in _TICKER_HINT.findall(text):
        if m not in _TICKER_FALSE_POSITIVES:
            hits += 1
    lower = text.lower()
    for name in speaker_hints:
        if name and name.lower() in lower:
            hits += 3  # speaker hits weigh more than generic capitalized tokens
    return hits


def _truncate_chunks(
    chunks: list[dict[str, Any]],
    speaker_hints: list[str],
    *,
    budget_chars: int = MAX_INPUT_CHARS,
) -> list[dict[str, Any]]:
    """Keep top-scoring chunks until under budget; re-sort by chunk_idx."""
    total = sum(len(c.get("text") or "") for c in chunks)
    if total <= budget_chars:
        return chunks
    scored = sorted(
        chunks,
        key=lambda c: (-_chunk_score(c.get("text") or "", speaker_hints),
                       int(c.get("chunk_idx") or 0)),
    )
    keep: list[dict[str, Any]] = []
    running = 0
    for c in scored:
        clen = len(c.get("text") or "")
        if running + clen > budget_chars and keep:
            break
        keep.append(c)
        running += clen
    keep.sort(key=lambda c: int(c.get("chunk_idx") or 0))
    return keep


# --------------------------------------------------------------------- #
# Public API                                                            #
# --------------------------------------------------------------------- #


def summarize_episode(
    *,
    episode: dict[str, Any],
    chunks: list[dict[str, Any]],
    client: Any,
    model: str = DEFAULT_MODEL,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce a structured summary for one episode.

    ``client`` is an ``anthropic.Anthropic`` (or a stub exposing
    ``messages.create``). ``cache`` is an optional in-memory dict keyed
    by ``episode_id``; if a hit is found, the LLM call is skipped.
    """
    episode_id = str(episode.get("episode_id") or "")
    if cache is not None and episode_id in cache:
        return cache[episode_id]

    speaker_hints = _speaker_hints_from_episode(episode)
    selected = _truncate_chunks(chunks, speaker_hints)
    transcript = _render_transcript(selected)
    metadata_block = _render_metadata(episode)

    base_prompt = (
        f"{_BASE_INSTRUCTIONS}\n\nSCHEMA:\n{_SCHEMA_DOC}\n\n"
        f"EPISODE METADATA:\n{metadata_block}\n\n"
        f"TRANSCRIPT:\n{transcript}\n\nJSON:\n"
    )

    summary = _call_with_retry(
        client=client,
        model=model,
        base_prompt=base_prompt,
        episode_id=episode_id,
        episode=episode,
        chunks=chunks,
    )
    if cache is not None:
        cache[episode_id] = summary
    return summary


# --------------------------------------------------------------------- #
# Internals                                                             #
# --------------------------------------------------------------------- #


def _speaker_hints_from_episode(episode: dict[str, Any]) -> list[str]:
    """Best-effort hint list for the truncation scorer."""
    hints: list[str] = []
    primary = episode.get("primary_speaker")
    if isinstance(primary, str) and primary:
        # speaker_ids look like "jensen_huang" — turn into "jensen huang"
        hints.append(primary.replace("_", " "))
    for co in episode.get("co_speakers") or []:
        if isinstance(co, str) and co:
            hints.append(co.replace("_", " "))
    # Title tokens are also a useful signal.
    title = (episode.get("title") or "").strip()
    if title:
        hints.append(title)
    return hints


def _render_transcript(chunks: list[dict[str, Any]]) -> str:
    """Render chunks with [chunk N | HH:MM:SS] inline markers."""
    out: list[str] = []
    for c in chunks:
        idx = int(c.get("chunk_idx") or 0)
        start = float(c.get("start_s") or 0.0)
        text = (c.get("text") or "").strip()
        if not text:
            continue
        out.append(f"[chunk {idx} | {_hhmmss(start)}] {text}")
    return "\n\n".join(out)


def _hhmmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _render_metadata(episode: dict[str, Any]) -> str:
    speakers = [str(episode.get("primary_speaker") or "").strip()]
    speakers += [str(x) for x in (episode.get("co_speakers") or [])]
    speakers = [s for s in speakers if s]
    return (
        f"episode_id: {episode.get('episode_id', '')}\n"
        f"channel: {episode.get('channel', '')}\n"
        f"title: {episode.get('title', '')}\n"
        f"speakers tagged at ingestion: {speakers}\n"
        f"published_utc: {episode.get('published_utc', '')}"
    )


def _call_with_retry(
    *,
    client: Any,
    model: str,
    base_prompt: str,
    episode_id: str,
    episode: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Call → validate → retry once → stub. Never raises."""
    for attempt in (0, 1):
        prompt = base_prompt if attempt == 0 else f"{_RETRY_PREAMBLE}{base_prompt}"
        try:
            raw = _invoke(client, model, prompt)
        except Exception as exc:
            log.warning("episode %s: LLM call raised (attempt %d): %s",
                        episode_id, attempt, exc)
            continue
        parsed = _try_parse(raw)
        if parsed is None:
            log.warning("episode %s: validation failed (attempt %d)",
                        episode_id, attempt)
            continue
        # Backfill deep_links if model left them empty.
        parsed["deep_links"] = _deep_links(episode, chunks, parsed.get("key_chunks") or [])
        return parsed
    return _stub_summary(episode)


def _invoke(client: Any, model: str, prompt: str) -> str:
    """Call ``messages.create`` and return the first text block."""
    resp = client.messages.create(
        model=model,
        max_tokens=DEFAULT_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    content = getattr(resp, "content", None) or []
    for block in content:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    raise ValueError("no text block in response")


def _try_parse(raw: str) -> dict[str, Any] | None:
    """Strip code fences, parse JSON, validate against EpisodeSummary."""
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        return EpisodeSummary.model_validate(data).model_dump()
    except ValidationError:
        return None


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # remove leading ``` line and trailing ```
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def _stub_summary(episode: dict[str, Any]) -> dict[str, Any]:
    """Fallback when both LLM attempts fail validation."""
    return EpisodeSummary(
        episode_id=str(episode.get("episode_id", "")),
        title=str(episode.get("title", "")),
        channel=str(episode.get("channel", "")),
        speakers=_speaker_ids(episode),
        summary="(LLM extraction failed; see chunks for raw content)",
        tickers=[],
        themes=[],
        notable_claims=[],
        tone="neutral",
        key_chunks=[],
        deep_links=[],
    ).model_dump()


def _speaker_ids(episode: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    p = episode.get("primary_speaker")
    if isinstance(p, str) and p:
        ids.append(p)
    for co in episode.get("co_speakers") or []:
        if isinstance(co, str) and co:
            ids.append(co)
    return ids


def _deep_links(
    episode: dict[str, Any],
    chunks: list[dict[str, Any]],
    key_chunks: list[int],
) -> list[str]:
    """Build timestamped deep-links for the key chunks.

    YouTube → ``youtu.be/<vid>?t=<seconds>``; podcasts get the episode
    URL with a hash-fragment timestamp (Apple Podcasts honors it,
    others ignore — harmless either way).
    """
    by_idx = {int(c.get("chunk_idx") or 0): c for c in chunks}
    url = (episode.get("url") or "").strip()
    source = (episode.get("source") or "").strip()
    out: list[str] = []
    for idx in key_chunks:
        c = by_idx.get(int(idx))
        if c is None:
            continue
        start = int(float(c.get("start_s") or 0.0))
        if source == "youtube":
            vid = _yt_vid(url) or str(episode.get("episode_id") or "")
            out.append(f"https://youtu.be/{vid}?t={start}")
        else:
            sep = "&" if "?" in url else "?"
            out.append(f"{url}{sep}t={start}" if url else f"#chunk-{idx}")
    return out


def _yt_vid(url: str) -> str | None:
    m = re.search(r"[?&]v=([\w-]{11})", url)
    if m:
        return m.group(1)
    m = re.search(r"youtu\.be/([\w-]{11})", url)
    return m.group(1) if m else None
