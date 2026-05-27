"""``NarrativeEvent`` dataclass and the SQL schema it serializes to.

Source-indifferent: one row per (source, provider_id, chunk_id, symbol).
Transcript chunks emit one row per mentioned ticker; news articles emit
one row per ticker on the article; X posts emit one row per ticker on
the post. The scorer consumes these uniformly downstream.

Rows are immutable. Re-scoring with a new sentiment or ticker-match
model writes new rows with a different ``model_version`` so historical
backtests can pick the version that would have existed at their
``as_of`` timestamp.

The companion SQL schema (see :data:`EVENTS_SCHEMA_SQL`) is applied by
``NarrativeEventStore`` in Step 2; Step 1 only defines the contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime


# --------------------------------------------------------------------------- #
# SQL                                                                         #
# --------------------------------------------------------------------------- #

EVENTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    theme TEXT,
    polarity REAL NOT NULL,
    published_utc TEXT NOT NULL,         -- ISO-8601 UTC string
    source TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    chunk_id TEXT,
    speaker TEXT,
    authority_weight REAL NOT NULL DEFAULT 1.0,
    ticker_match_confidence REAL NOT NULL DEFAULT 1.0,
    text_excerpt TEXT NOT NULL,
    ingested_utc TEXT NOT NULL,
    model_version TEXT NOT NULL,
    themes_hash TEXT NOT NULL,
    authority_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_symbol_time
    ON events(symbol, published_utc);

CREATE INDEX IF NOT EXISTS idx_events_theme_time
    ON events(theme, published_utc) WHERE theme IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_events_source_time
    ON events(source, published_utc);

CREATE INDEX IF NOT EXISTS idx_events_time
    ON events(published_utc);

CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

EVENTS_SCHEMA_VERSION = "1"


# Column order for ``to_row`` / ``from_row``. Matches the CREATE TABLE
# above so an ``executemany(INSERT ...)`` can use ``to_row()`` directly.
_ROW_COLUMNS: tuple[str, ...] = (
    "id",
    "symbol",
    "theme",
    "polarity",
    "published_utc",
    "source",
    "provider_id",
    "chunk_id",
    "speaker",
    "authority_weight",
    "ticker_match_confidence",
    "text_excerpt",
    "ingested_utc",
    "model_version",
    "themes_hash",
    "authority_hash",
)


# --------------------------------------------------------------------------- #
# Dataclass                                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NarrativeEvent:
    """One structured narrative claim about one ticker at one moment in time.

    Out-of-range ``polarity`` (outside ``[-1.0, +1.0]``) raises
    ``ValueError`` in ``__post_init__`` rather than being clamped. A
    polarity outside the unit interval is a producer bug — a sign-flip
    or scaling error in the sentiment model — and silently clamping it
    would mask the underlying defect. The store should never see such a
    value; failing loud at the write boundary forces the upstream caller
    to fix it.
    """

    id: str                          # sha256(source|provider_id|chunk_id|symbol)[:32]
    symbol: str                      # uppercased ticker
    theme: str | None                # theme name from themes.py; None if no theme matched
    polarity: float                  # [-1.0, +1.0]
    published_utc: datetime          # the only as-of anchor
    source: str                      # e.g. "polygon" | "x" | "youtube_transcript"
    provider_id: str                 # polygon article id, youtube video id, podcast guid
    chunk_id: str | None             # for transcript sources; None for article-level sources
    speaker: str | None              # for transcripts; None for articles
    authority_weight: float          # 1.0 default; >1 for tier-1 speakers; <1 for noisier sources
    ticker_match_confidence: float   # [0, 1] — how confident the ticker matcher is
    text_excerpt: str                # short excerpt for audit/dashboard display, NOT for re-scoring
    ingested_utc: datetime           # when this row was written; audit-only, never used for as-of
    model_version: str               # sentiment model version (e.g. "polarity-v1")
    themes_hash: str                 # sha256(themes.py contents) at write time
    authority_hash: str              # sha256(speaker_authority.py contents) at write time

    def __post_init__(self) -> None:
        if not -1.0 <= self.polarity <= 1.0:
            raise ValueError(
                f"polarity must be in [-1.0, +1.0], got {self.polarity!r}"
            )

    @staticmethod
    def compute_id(
        *,
        source: str,
        provider_id: str,
        chunk_id: str | None,
        symbol: str,
    ) -> str:
        """Deterministic 32-hex-char ID for ``(source, provider_id, chunk_id, symbol)``.

        Making this a pure function of the identity tuple is what gives
        the store its idempotency: re-running ingestion never duplicates
        events, and an event from a Polygon article about NVDA has a
        different ID than an event from a transcript chunk that mentions
        NVDA even if the underlying news is the same.
        """
        payload = f"{source}|{provider_id}|{chunk_id or ''}|{symbol}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        theme: str | None,
        polarity: float,
        published_utc: datetime,
        source: str,
        provider_id: str,
        chunk_id: str | None,
        speaker: str | None,
        authority_weight: float,
        ticker_match_confidence: float,
        text_excerpt: str,
        ingested_utc: datetime,
        model_version: str,
        themes_hash: str,
        authority_hash: str,
    ) -> "NarrativeEvent":
        """Construct an event, auto-computing ``id`` and uppercasing ``symbol``.

        Prefer this over the dataclass constructor for new events. The
        bare constructor is reserved for ``from_row`` and tests that
        need to exercise the validation behavior with a specific ID.
        """
        upper_symbol = symbol.upper()
        event_id = cls.compute_id(
            source=source,
            provider_id=provider_id,
            chunk_id=chunk_id,
            symbol=upper_symbol,
        )
        return cls(
            id=event_id,
            symbol=upper_symbol,
            theme=theme,
            polarity=polarity,
            published_utc=published_utc,
            source=source,
            provider_id=provider_id,
            chunk_id=chunk_id,
            speaker=speaker,
            authority_weight=authority_weight,
            ticker_match_confidence=ticker_match_confidence,
            text_excerpt=text_excerpt,
            ingested_utc=ingested_utc,
            model_version=model_version,
            themes_hash=themes_hash,
            authority_hash=authority_hash,
        )

    def to_row(self) -> tuple:
        """Serialize to a SQL row tuple matching :data:`_ROW_COLUMNS` order.

        Datetimes are written as ISO-8601 strings; ``from_row`` parses
        them back via :meth:`datetime.fromisoformat`.
        """
        return (
            self.id,
            self.symbol,
            self.theme,
            self.polarity,
            self.published_utc.isoformat(),
            self.source,
            self.provider_id,
            self.chunk_id,
            self.speaker,
            self.authority_weight,
            self.ticker_match_confidence,
            self.text_excerpt,
            self.ingested_utc.isoformat(),
            self.model_version,
            self.themes_hash,
            self.authority_hash,
        )

    @classmethod
    def from_row(cls, row: tuple) -> "NarrativeEvent":
        """Deserialize a SQL row tuple produced by :meth:`to_row`."""
        return cls(
            id=row[0],
            symbol=row[1],
            theme=row[2],
            polarity=row[3],
            published_utc=datetime.fromisoformat(row[4]),
            source=row[5],
            provider_id=row[6],
            chunk_id=row[7],
            speaker=row[8],
            authority_weight=row[9],
            ticker_match_confidence=row[10],
            text_excerpt=row[11],
            ingested_utc=datetime.fromisoformat(row[12]),
            model_version=row[13],
            themes_hash=row[14],
            authority_hash=row[15],
        )
