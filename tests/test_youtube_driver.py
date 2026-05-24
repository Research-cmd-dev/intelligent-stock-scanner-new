"""Tests for the driver.run_backfill() entry point (Phase 3.5).

All tests mock discovery and fetch at module-namespace seams so we never
load Whisper, never download MP3s, and never hit a real podcast feed.
The transcript store is real (in :memory:-like tmp_path) so we can
assert the ``is_backfill`` column was populated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.narrative.sources.youtube import driver
from src.narrative.sources.youtube.chunker import Chunk, Segment
from src.narrative.sources.youtube.discovery import VideoCandidate
from src.narrative.sources.youtube.transcript_fetchers import TranscriptResult
from src.narrative.sources.youtube.transcript_store import TranscriptStore


def _candidate(video_id: str, matched_speaker: str = "jensen_huang") -> VideoCandidate:
    return VideoCandidate(
        channel="Lex Fridman Podcast",
        channel_id="podcast:lex_fridman_podcast",
        video_id=video_id,
        url=f"https://lexfridman.com/{video_id}",
        title="Jensen Huang on AI",
        published_utc=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
        reason=f"speaker:{matched_speaker}",
        matched_speaker=matched_speaker,
        matched_tickers=("NVDA",),
        matched_text="[TITLE] Jensen Huang on AI",
        match_source="title",
        source="podcast",
        audio_url="https://media.example/ep.mp3",
        episode_guid=video_id,
    )


def _fake_transcript_result() -> TranscriptResult:
    segments = (
        Segment(0.0, 30.0, "Jensen says compute is the bottleneck for AI scaling at GPU clusters."),
        Segment(30.0, 60.0, "He references Blackwell and the new inference economics."),
    )
    return TranscriptResult(segments=segments, source_method="podcast_rss", duration_s=60.0)


# --------------------------------------------------------------------- #
# 1. dry_run reports cost without calling fetch_transcript               #
# --------------------------------------------------------------------- #


def test_run_backfill_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        driver, "discover_podcast_candidates",
        lambda *a, **kw: [_candidate(f"pc_test{i:07d}") for i in range(10)],
    )
    monkeypatch.setattr(
        driver, "fetch_transcript",
        lambda *a, **kw: pytest.fail("fetch_transcript must not run in dry-run"),
    )
    monkeypatch.setattr(
        driver, "_load_whisper",
        lambda _: pytest.fail("whisper must not load in dry-run"),
    )

    db = tmp_path / "test.db"
    result = driver.run_backfill(
        db_path=db,
        lookback_days=540,
        max_episodes=500,
        speaker_tiers=frozenset({1}),
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["is_backfill"] is True
    assert result["candidates_count"] == 10
    assert result["after_filter"] == 10
    assert result["estimated_cost_usd"]["low"] == round(10 * 0.075, 2)
    assert result["estimated_cost_usd"]["high"] >= result["estimated_cost_usd"]["low"]


# --------------------------------------------------------------------- #
# 2. is_backfill = 1 lands on every backfill row                         #
# --------------------------------------------------------------------- #


def test_run_backfill_marks_is_backfill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver, "discover_podcast_candidates",
        lambda *a, **kw: [_candidate("pc_marker001")],
    )
    monkeypatch.setattr(driver, "_load_whisper", lambda _: object())
    monkeypatch.setattr(driver, "fetch_transcript",
                        lambda *a, **kw: _fake_transcript_result())

    db = tmp_path / "test.db"
    result = driver.run_backfill(
        db_path=db,
        speaker_tiers=frozenset({1}),
    )
    assert result["processed"] == 1
    with TranscriptStore(db) as store:
        row = store._conn.execute(
            "SELECT episode_id, is_backfill FROM episodes WHERE episode_id='pc_marker001'"
        ).fetchone()
    assert row == ("pc_marker001", 1)


# --------------------------------------------------------------------- #
# 3. max_episodes truncates the candidate list                           #
# --------------------------------------------------------------------- #


def test_run_backfill_respects_max_episodes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver, "discover_podcast_candidates",
        lambda *a, **kw: [_candidate(f"pc_cap{i:08d}") for i in range(100)],
    )
    monkeypatch.setattr(driver, "_load_whisper", lambda _: object())
    monkeypatch.setattr(driver, "fetch_transcript",
                        lambda *a, **kw: _fake_transcript_result())

    db = tmp_path / "test.db"
    result = driver.run_backfill(
        db_path=db,
        max_episodes=5,
        speaker_tiers=frozenset({1}),
    )
    assert result["processed"] == 5
    with TranscriptStore(db) as store:
        n = store._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    assert n == 5


# --------------------------------------------------------------------- #
# 4. has_episode dedup prevents re-processing existing rows              #
# --------------------------------------------------------------------- #


def test_run_backfill_skips_existing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "test.db"

    pre = _candidate("pc_existing01")
    pre_chunk = Chunk(0, 0.0, 30.0, "pre-existing chunk text " * 20)
    with TranscriptStore(db) as store:
        store.write_episode(
            pre, chunks=[pre_chunk], duration_s=30.0,
            source_method="podcast_rss", is_backfill=False,
        )

    monkeypatch.setattr(
        driver, "discover_podcast_candidates",
        lambda *a, **kw: [pre, _candidate("pc_new00001"), _candidate("pc_new00002")],
    )
    monkeypatch.setattr(driver, "_load_whisper", lambda _: object())
    monkeypatch.setattr(driver, "fetch_transcript",
                        lambda *a, **kw: _fake_transcript_result())

    result = driver.run_backfill(
        db_path=db,
        speaker_tiers=frozenset({1}),
    )
    assert result["processed"] == 2
    assert result["after_filter"] == 2
    with TranscriptStore(db) as store:
        backfilled = store._conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE is_backfill=1"
        ).fetchone()[0]
        total = store._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    assert total == 3      # 1 pre + 2 new
    assert backfilled == 2  # only the new ones


# --------------------------------------------------------------------- #
# Bonus: max_episodes guard                                              #
# --------------------------------------------------------------------- #


def test_run_backfill_rejects_zero_max_episodes(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        driver.run_backfill(db_path=tmp_path / "x.db", max_episodes=0)
