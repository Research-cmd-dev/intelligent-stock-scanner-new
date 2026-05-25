"""Tests for the per-episode Claude Haiku summarizer.

The anthropic client is mocked end-to-end — no real API calls. The
mock exposes ``messages.create`` and returns either a canned JSON
string or raises, depending on what each test wants to verify.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.narrative.briefing.llm_summarizer import (
    MAX_INPUT_CHARS,
    EpisodeSummary,
    _truncate_chunks,
    summarize_episode,
)


# --------------------------------------------------------------------- #
# Mock client                                                           #
# --------------------------------------------------------------------- #


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_TextBlock(text)]


class _Messages:
    def __init__(self, responses: list[Any]) -> None:
        # Each entry: either a JSON string (returned) or an Exception (raised).
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("test mock ran out of responses")
        out = self._responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return _Response(out)


class _Client:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _Messages(responses)


# --------------------------------------------------------------------- #
# Fixtures                                                              #
# --------------------------------------------------------------------- #


def _episode(**overrides: Any) -> dict[str, Any]:
    base = {
        "episode_id": "pc_test_001",
        "source": "podcast",
        "channel": "Bg2 Pod",
        "channel_id": "podcast:bg2_pod",
        "title": "Jensen on AI factories",
        "url": "https://example.com/ep001",
        "audio_url": "https://example.com/ep001.mp3",
        "published_utc": "2026-05-23T12:00:00+00:00",
        "duration_s": 3600.0,
        "primary_speaker": "jensen_huang",
        "co_speakers": ["brad_gerstner"],
        "reason": "speaker:jensen_huang",
        "source_method": "podcast_rss",
        "ingested_at": "2026-05-24T03:00:00+00:00",
        "is_backfill": False,
    }
    base.update(overrides)
    return base


def _chunks(n: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "chunk_idx": i,
            "start_s": i * 600.0,
            "end_s": (i + 1) * 600.0,
            "text": f"Chunk {i} discussing NVDA and AI compute infrastructure.",
        }
        for i in range(n)
    ]


def _valid_json(episode_id: str = "pc_test_001") -> str:
    return json.dumps({
        "episode_id": episode_id,
        "title": "Jensen on AI factories",
        "channel": "Bg2 Pod",
        "speakers": ["jensen_huang"],
        "summary": "Three sentences about NVDA scale. Second sentence. Third.",
        "tickers": [
            {"symbol": "NVDA", "sentiment": 0.8, "mentions": 7, "context": "AI factory scaling"},
            {"symbol": "TSM", "sentiment": 0.6, "mentions": 3, "context": "Supplier capacity"},
        ],
        "themes": ["AI capex", "sovereign AI"],
        "notable_claims": [
            {"claim": "AI factories will be 10x larger by 2028",
             "speaker": "jensen_huang", "polarity": 0.8},
        ],
        "tone": "bullish",
        "key_chunks": [0, 2, 4],
        "deep_links": [],
    })


# --------------------------------------------------------------------- #
# 1. Extracts tickers from a valid response                             #
# --------------------------------------------------------------------- #


def test_summarize_episode_extracts_tickers() -> None:
    client = _Client([_valid_json()])
    result = summarize_episode(episode=_episode(), chunks=_chunks(), client=client)
    assert result["episode_id"] == "pc_test_001"
    symbols = [t["symbol"] for t in result["tickers"]]
    assert symbols == ["NVDA", "TSM"]
    assert result["tickers"][0]["sentiment"] == 0.8
    assert result["tickers"][0]["mentions"] == 7
    assert result["tone"] == "bullish"
    assert len(client.messages.calls) == 1


# --------------------------------------------------------------------- #
# 2. Retry path on first-call malformed JSON                            #
# --------------------------------------------------------------------- #


def test_summarize_episode_handles_malformed_json() -> None:
    client = _Client([
        "this is not json at all, sorry",     # first attempt: invalid
        _valid_json(),                         # second attempt: valid
    ])
    result = summarize_episode(episode=_episode(), chunks=_chunks(), client=client)
    assert result["summary"].startswith("Three sentences")
    assert len(client.messages.calls) == 2
    # The retry prompt should differ from the first one.
    first = client.messages.calls[0]["messages"][0]["content"]
    second = client.messages.calls[1]["messages"][0]["content"]
    assert second != first
    assert "failed validation" in second.lower() or "no commentary" in second.lower()


# --------------------------------------------------------------------- #
# 3. Truncation: long input gets cut to ~MAX_INPUT_CHARS                #
# --------------------------------------------------------------------- #


def test_summarize_episode_truncates_long_input() -> None:
    # 25 chunks × 10k chars = 250k chars input → must truncate.
    long_text_nvda = ("NVDA matters here. " * 600)[:10_000]
    long_text_plain = ("filler word filler word " * 500)[:10_000]
    chunks = []
    for i in range(25):
        # Make every 4th chunk ticker-rich so the scorer prefers them.
        text = long_text_nvda if i % 4 == 0 else long_text_plain
        chunks.append({"chunk_idx": i, "start_s": i * 600.0,
                       "end_s": (i + 1) * 600.0, "text": text})

    kept = _truncate_chunks(chunks, speaker_hints=["jensen huang"],
                            budget_chars=MAX_INPUT_CHARS)
    total_chars = sum(len(c["text"]) for c in kept)
    assert total_chars <= MAX_INPUT_CHARS
    # Must keep at least one chunk.
    assert len(kept) >= 1
    # Ticker-rich chunks (i % 4 == 0) should dominate the kept set.
    ticker_rich_kept = sum(1 for c in kept if c["chunk_idx"] % 4 == 0)
    plain_kept = len(kept) - ticker_rich_kept
    assert ticker_rich_kept >= plain_kept, (
        f"truncation should prefer ticker-rich chunks: "
        f"kept {ticker_rich_kept} rich vs {plain_kept} plain"
    )
    # Output preserves chunk_idx order.
    indices = [c["chunk_idx"] for c in kept]
    assert indices == sorted(indices)


# --------------------------------------------------------------------- #
# 4. Both LLM attempts fail → stub returned, no exception               #
# --------------------------------------------------------------------- #


def test_summarize_episode_returns_stub_on_double_failure() -> None:
    client = _Client(["garbage 1", "garbage 2"])
    result = summarize_episode(episode=_episode(), chunks=_chunks(), client=client)
    assert result["episode_id"] == "pc_test_001"
    assert "LLM extraction failed" in result["summary"]
    assert result["tickers"] == []
    assert result["tone"] == "neutral"
    # Both attempts were made.
    assert len(client.messages.calls) == 2


# --------------------------------------------------------------------- #
# 5. Cache hit skips the LLM call                                       #
# --------------------------------------------------------------------- #


def test_summarize_episode_cache_hit_skips_llm() -> None:
    pre = {"pc_test_001": {"episode_id": "pc_test_001", "summary": "cached"}}
    client = _Client([])  # would raise if called
    result = summarize_episode(
        episode=_episode(), chunks=_chunks(), client=client, cache=pre,
    )
    assert result == pre["pc_test_001"]
    assert client.messages.calls == []


# --------------------------------------------------------------------- #
# 6. Pydantic tone validator coerces unknown values to "neutral"        #
# --------------------------------------------------------------------- #


def test_episode_summary_tone_coercion() -> None:
    obj = EpisodeSummary(
        episode_id="x", title="t", channel="c",
        summary="s", tone="ECSTATIC",
    )
    assert obj.tone == "neutral"
    for valid in ("bullish", "bearish", "mixed", "neutral"):
        obj = EpisodeSummary(episode_id="x", title="t", channel="c",
                             summary="s", tone=valid)
        assert obj.tone == valid
