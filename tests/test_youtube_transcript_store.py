"""Tests for the SQLite + FTS5 transcript store.

All tests use ``:memory:`` so nothing touches disk.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.narrative.sources.youtube.chunker import Chunk
from src.narrative.sources.youtube.discovery import VideoCandidate
from src.narrative.sources.youtube.transcript_store import TranscriptStore


def _candidate(video_id: str = "vid00000001", *, source: str = "youtube") -> VideoCandidate:
    return VideoCandidate(
        channel="Lex Fridman Podcast",
        channel_id="UCSHZKyawb77ixDdsGog4iWA",
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="Jensen Huang on the future of AI compute",
        published_utc=datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc),
        reason="speaker:jensen_huang",
        matched_speaker="jensen_huang",
        matched_tickers=("NVDA", "TSM"),
        matched_text="[TITLE] Jensen Huang on the future...",
        match_source="title",
        co_speakers=("brad_gerstner",),
        source=source,
        audio_url=("https://cdn.example.com/ep.mp3" if source == "podcast" else None),
        episode_guid=("guid-abc-123" if source == "podcast" else None),
    )


def _chunks(n: int = 3) -> list[Chunk]:
    return [
        Chunk(
            idx=i,
            start_s=float(i * 55),
            end_s=float(i * 55 + 60),
            text=f"Chunk {i}: Jensen says the next inference cycle will hit a billion tokens per second.",
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------- #
# 1. Schema initializes from a blank :memory: connection                #
# --------------------------------------------------------------------- #


def test_schema_initializes_clean() -> None:
    with TranscriptStore(":memory:") as store:
        tables = [
            r[0] for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index','trigger') "
                "ORDER BY name"
            ).fetchall()
        ]
    for required in ("episodes", "chunks", "chunks_fts", "ingest_log",
                     "chunks_ai", "chunks_ad",
                     "ix_episodes_published", "ix_episodes_speaker",
                     "ix_episodes_source", "ix_ingest_log_episode"):
        assert required in tables, f"missing {required}; have {tables}"


# --------------------------------------------------------------------- #
# 2. Write + read round-trip                                            #
# --------------------------------------------------------------------- #


def test_write_episode_round_trip() -> None:
    with TranscriptStore(":memory:") as store:
        cand = _candidate()
        chunks = _chunks(3)
        store.write_episode(cand, chunks=chunks, duration_s=180.0, source_method="transcript_api")

        assert store.has_episode(cand.video_id)
        loaded = store.chunks_for_episode(cand.video_id)
        assert loaded == chunks

        row = store._conn.execute(
            "SELECT source, primary_speaker, co_speakers, duration_s, source_method "
            "FROM episodes WHERE episode_id = ?", (cand.video_id,)
        ).fetchone()
        assert row[0] == "youtube"
        assert row[1] == "jensen_huang"
        assert json.loads(row[2]) == ["brad_gerstner"]
        assert row[3] == 180.0
        assert row[4] == "transcript_api"


# --------------------------------------------------------------------- #
# 3. FTS5 search finds inserted chunks                                  #
# --------------------------------------------------------------------- #


def test_fts5_search_finds_chunks() -> None:
    with TranscriptStore(":memory:") as store:
        store.write_episode(
            _candidate(),
            chunks=_chunks(3),
            duration_s=180.0,
            source_method="transcript_api",
        )
        hits = store.search_chunks("inference", limit=10)
        assert len(hits) >= 1
        # tuple shape: (episode_id, chunk_idx, start_s, end_s, text)
        assert all("inference" in row[4].lower() for row in hits)


# --------------------------------------------------------------------- #
# 4. Idempotent rewrite (same episode_id) — no duplicate rows           #
# --------------------------------------------------------------------- #


def test_write_episode_is_idempotent() -> None:
    with TranscriptStore(":memory:") as store:
        cand = _candidate()
        store.write_episode(cand, chunks=_chunks(3), duration_s=180.0, source_method="transcript_api")
        # Second write with different chunk count — should fully replace
        store.write_episode(cand, chunks=_chunks(5), duration_s=300.0, source_method="whisper")

        ep_count = store._conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE episode_id = ?", (cand.video_id,)
        ).fetchone()[0]
        chunk_count = store._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE episode_id = ?", (cand.video_id,)
        ).fetchone()[0]
        fts_count = store._conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'inference'"
        ).fetchone()[0]
        assert ep_count == 1
        assert chunk_count == 5
        # FTS rows must be exactly chunk_count — no stale 3-chunk leftovers
        assert fts_count == 5

        # And the new source_method/duration won
        row = store._conn.execute(
            "SELECT source_method, duration_s FROM episodes WHERE episode_id = ?",
            (cand.video_id,)
        ).fetchone()
        assert row == ("whisper", 300.0)


# --------------------------------------------------------------------- #
# 5. log_attempt + last_attempt round trip                              #
# --------------------------------------------------------------------- #


def test_log_attempt_and_last_attempt() -> None:
    with TranscriptStore(":memory:") as store:
        ep = "vid_logtest1"
        assert store.last_attempt(ep) is None

        store.log_attempt(ep, status="failed", source_method="transcript_api", error="429")
        # Ensure later log is, well, later
        import time
        time.sleep(0.005)
        store.log_attempt(ep, status="ok", source_method="yt_dlp_subs")

        last = store.last_attempt(ep)
        assert last is not None
        ts, status = last
        assert status == "ok"
        assert (datetime.now(timezone.utc) - ts) < timedelta(minutes=1)


# --------------------------------------------------------------------- #
# Bonus: podcast candidate carries audio_url through                    #
# --------------------------------------------------------------------- #


def test_podcast_candidate_audio_url_persisted() -> None:
    with TranscriptStore(":memory:") as store:
        cand = _candidate(video_id="pc_abc12345678", source="podcast")
        store.write_episode(cand, chunks=_chunks(2), duration_s=120.0, source_method="podcast_rss")
        row = store._conn.execute(
            "SELECT source, audio_url FROM episodes WHERE episode_id = ?",
            (cand.video_id,),
        ).fetchone()
        assert row == ("podcast", "https://cdn.example.com/ep.mp3")


# --------------------------------------------------------------------- #
# is_backfill column (Phase 3.5)                                         #
# --------------------------------------------------------------------- #


def test_is_backfill_column_present_on_fresh_db() -> None:
    with TranscriptStore(":memory:") as store:
        cols = {row[1] for row in store._conn.execute(
            "PRAGMA table_info(episodes)"
        ).fetchall()}
    assert "is_backfill" in cols


def test_is_backfill_distinguishes_rows() -> None:
    with TranscriptStore(":memory:") as store:
        live = _candidate(video_id="live0000001")
        back = _candidate(video_id="back0000001")
        store.write_episode(live, chunks=_chunks(1), duration_s=60.0,
                            source_method="podcast_rss")
        store.write_episode(back, chunks=_chunks(1), duration_s=60.0,
                            source_method="podcast_rss", is_backfill=True)

        rows = store._conn.execute(
            "SELECT episode_id, is_backfill FROM episodes ORDER BY episode_id"
        ).fetchall()
        assert rows == [("back0000001", 1), ("live0000001", 0)]


def test_is_backfill_migration_idempotent_on_pre_phase_3_5_db(tmp_path) -> None:
    """A DB that pre-dates Phase 3.5 (no is_backfill column) must get
    the column added on first instantiation of the new store, without
    losing any existing rows. Re-running the migration is a no-op."""
    db_path = tmp_path / "legacy.db"
    # Hand-build a pre-3.5 schema (episodes without is_backfill column)
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE episodes (
          episode_id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          channel TEXT NOT NULL,
          channel_id TEXT NOT NULL,
          title TEXT NOT NULL,
          url TEXT NOT NULL,
          audio_url TEXT,
          published_utc TIMESTAMP NOT NULL,
          duration_s REAL,
          primary_speaker TEXT,
          co_speakers TEXT,
          reason TEXT NOT NULL,
          source_method TEXT NOT NULL,
          ingested_at TIMESTAMP NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO episodes VALUES "
        "('legacy0001', 'podcast', 'ch', 'cid', 't', 'u', NULL, '2024-01-01', "
        " NULL, NULL, '[]', 'r', 'podcast_rss', '2024-01-02')"
    )
    conn.commit()
    conn.close()

    # Open via the new store — migration should run
    with TranscriptStore(db_path) as store:
        cols = {row[1] for row in store._conn.execute(
            "PRAGMA table_info(episodes)"
        ).fetchall()}
        assert "is_backfill" in cols
        row = store._conn.execute(
            "SELECT episode_id, is_backfill FROM episodes WHERE episode_id='legacy0001'"
        ).fetchone()
        assert row == ("legacy0001", 0)  # default value on existing rows

    # Re-open: migration check should be a no-op
    with TranscriptStore(db_path) as store:
        row = store._conn.execute(
            "SELECT COUNT(*) FROM episodes"
        ).fetchone()
        assert row == (1,)
