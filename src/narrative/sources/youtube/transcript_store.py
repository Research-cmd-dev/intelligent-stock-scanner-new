"""Persistent transcript store backed by SQLite + FTS5.

Holds one row per ``episodes`` and N rows per ``chunks``; the ``chunks_fts``
virtual table is kept in sync via two AFTER triggers so callers never
have to manage FTS state directly.

Schema migration (CREATE IF NOT EXISTS) runs on every connect, so there
is no separate migration tool — the store self-heals on first use.

The store is the *only* writer to this file. Phase 4 (event emitter) and
later phases read from it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chunker import Chunk
from .discovery import VideoCandidate


DEFAULT_DB_PATH = Path("data/narrative/transcripts.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  episode_id        TEXT PRIMARY KEY,
  source            TEXT NOT NULL,
  channel           TEXT NOT NULL,
  channel_id        TEXT NOT NULL,
  title             TEXT NOT NULL,
  url               TEXT NOT NULL,
  audio_url         TEXT,
  published_utc     TIMESTAMP NOT NULL,
  duration_s        REAL,
  primary_speaker   TEXT,
  co_speakers       TEXT,
  reason            TEXT NOT NULL,
  source_method     TEXT NOT NULL,
  ingested_at       TIMESTAMP NOT NULL,
  is_backfill       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_episodes_published ON episodes(published_utc DESC);
CREATE INDEX IF NOT EXISTS ix_episodes_speaker   ON episodes(primary_speaker);
CREATE INDEX IF NOT EXISTS ix_episodes_source    ON episodes(source);

CREATE TABLE IF NOT EXISTS chunks (
  episode_id  TEXT NOT NULL,
  chunk_idx   INTEGER NOT NULL,
  start_s     REAL NOT NULL,
  end_s       REAL NOT NULL,
  text        TEXT NOT NULL,
  PRIMARY KEY (episode_id, chunk_idx),
  FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  content='chunks',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;

CREATE TABLE IF NOT EXISTS ingest_log (
  episode_id    TEXT NOT NULL,
  attempted_at  TIMESTAMP NOT NULL,
  status        TEXT NOT NULL,
  source_method TEXT,
  error         TEXT,
  PRIMARY KEY (episode_id, attempted_at)
);

CREATE INDEX IF NOT EXISTS ix_ingest_log_episode ON ingest_log(episode_id, attempted_at DESC);
"""


class TranscriptStore:
    """Read/write façade over ``transcripts.db``.

    Use as a context manager when the lifetime is short (one ingestion
    run); otherwise keep an instance around for the process lifetime.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path: Path | str = db_path
        if db_path != ":memory:":
            p = Path(db_path)
            p.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            db_path if isinstance(db_path, str) else str(db_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None,  # we manage transactions explicitly
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._migrate_is_backfill_column()

    # ---- migrations ---------------------------------------------------- #

    def _migrate_is_backfill_column(self) -> None:
        """Add ``is_backfill`` to ``episodes`` if missing.

        Idempotent: a fresh CREATE TABLE already includes the column, so
        the check just confirms presence. Existing pre-Phase-3.5 DBs get
        the column added with a default of 0 (= live ingestion).
        """
        cur = self._conn.execute("PRAGMA table_info(episodes)")
        cols = {row[1] for row in cur.fetchall()}
        if "is_backfill" not in cols:
            self._conn.execute(
                "ALTER TABLE episodes "
                "ADD COLUMN is_backfill INTEGER NOT NULL DEFAULT 0"
            )

    # ---- context manager ---------------------------------------------- #

    def __enter__(self) -> "TranscriptStore":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # ---- read API ----------------------------------------------------- #

    def has_episode(self, episode_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM episodes WHERE episode_id = ? LIMIT 1",
            (episode_id,),
        ).fetchone()
        return row is not None

    def last_attempt(self, episode_id: str) -> tuple[datetime, str] | None:
        row = self._conn.execute(
            "SELECT attempted_at, status FROM ingest_log "
            "WHERE episode_id = ? ORDER BY attempted_at DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        if row is None:
            return None
        return (_parse_dt(row[0]), row[1])

    def chunks_for_episode(self, episode_id: str) -> list[Chunk]:
        rows = self._conn.execute(
            "SELECT chunk_idx, start_s, end_s, text FROM chunks "
            "WHERE episode_id = ? ORDER BY chunk_idx",
            (episode_id,),
        ).fetchall()
        return [Chunk(idx=r[0], start_s=r[1], end_s=r[2], text=r[3]) for r in rows]

    def search_chunks(
        self, query: str, *, limit: int = 20
    ) -> list[tuple[str, int, float, float, str]]:
        """Full-text search across all chunks.

        Returns tuples of ``(episode_id, chunk_idx, start_s, end_s, text)``
        ordered by FTS5's default relevance ranking.
        """
        rows = self._conn.execute(
            "SELECT c.episode_id, c.chunk_idx, c.start_s, c.end_s, c.text "
            "FROM chunks_fts JOIN chunks c ON chunks_fts.rowid = c.rowid "
            "WHERE chunks_fts MATCH ? LIMIT ?",
            (query, limit),
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    # ---- write API ---------------------------------------------------- #

    def write_episode(
        self,
        candidate: VideoCandidate,
        *,
        chunks: list[Chunk],
        duration_s: float | None,
        source_method: str,
        is_backfill: bool = False,
    ) -> None:
        """Write one episode and its chunks atomically.

        Idempotent: re-writing the same ``episode_id`` replaces the row
        and its chunks. Existing FTS rows are cleaned up via the
        ``chunks_ad`` trigger when old chunks get deleted.

        ``is_backfill=True`` flags the row as a historical-backfill
        ingestion (Phase 3.5) so downstream phases can distinguish it
        from live nightly runs.
        """
        ep_id = candidate.video_id
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("BEGIN")
        try:
            # Wipe any prior chunks first so the AD trigger keeps FTS clean.
            self._conn.execute("DELETE FROM chunks WHERE episode_id = ?", (ep_id,))
            self._conn.execute(
                "INSERT OR REPLACE INTO episodes "
                "(episode_id, source, channel, channel_id, title, url, audio_url, "
                " published_utc, duration_s, primary_speaker, co_speakers, reason, "
                " source_method, ingested_at, is_backfill) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ep_id,
                    candidate.source,
                    candidate.channel,
                    candidate.channel_id,
                    candidate.title,
                    candidate.url,
                    candidate.audio_url,
                    candidate.published_utc.isoformat(),
                    duration_s,
                    candidate.matched_speaker,
                    json.dumps(list(candidate.co_speakers)),
                    candidate.reason,
                    source_method,
                    now,
                    1 if is_backfill else 0,
                ),
            )
            self._conn.executemany(
                "INSERT INTO chunks (episode_id, chunk_idx, start_s, end_s, text) "
                "VALUES (?,?,?,?,?)",
                [(ep_id, c.idx, c.start_s, c.end_s, c.text) for c in chunks],
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def log_attempt(
        self,
        episode_id: str,
        *,
        status: str,
        source_method: str | None = None,
        error: str | None = None,
    ) -> None:
        """Append one row to ``ingest_log``.

        ``status`` is a free-text label (``"ok"``, ``"failed"``,
        ``"skipped"``, etc.) — the driver decides the vocabulary.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO ingest_log "
            "(episode_id, attempted_at, status, source_method, error) "
            "VALUES (?,?,?,?,?)",
            (episode_id, now, status, source_method, error),
        )


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #


def _parse_dt(s: Any) -> datetime:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    if isinstance(s, str):
        return datetime.fromisoformat(s)
    raise TypeError(f"unexpected datetime value: {type(s).__name__}")
