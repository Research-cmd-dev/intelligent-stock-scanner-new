"""Phase 4, Step 1: schema-level tests for ``NarrativeEvent``.

Covers the dataclass contract only — no SQLite, no store. The store
gets its own test file in Step 2.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.narrative.events import NarrativeEvent
from src.narrative.events.schema import EVENTS_SCHEMA_SQL


# ---------------------------------------------------------------------- #
# Fixtures                                                               #
# ---------------------------------------------------------------------- #


def _make_event(**overrides) -> NarrativeEvent:
    """Build a fully-populated event via the ``create`` factory.

    Tests override only the fields they care about, which keeps each
    test focused on one axis of behavior.
    """
    defaults = dict(
        symbol="NVDA",
        theme="AI Infrastructure",
        polarity=0.42,
        published_utc=datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc),
        source="youtube_transcript",
        provider_id="abc123video",
        chunk_id="abc123video#7",
        speaker="Jensen Huang",
        authority_weight=1.5,
        ticker_match_confidence=0.95,
        text_excerpt="Nvidia continues to dominate AI training workloads...",
        ingested_utc=datetime(2026, 5, 2, 1, 0, tzinfo=timezone.utc),
        model_version="polarity-v1",
        themes_hash="t" * 64,
        authority_hash="a" * 64,
    )
    defaults.update(overrides)
    return NarrativeEvent.create(**defaults)


# ---------------------------------------------------------------------- #
# Round-trip                                                             #
# ---------------------------------------------------------------------- #


def test_to_row_from_row_roundtrip_preserves_equality():
    """``from_row(to_row(e)) == e`` for a fully-populated event."""
    original = _make_event()
    rebuilt = NarrativeEvent.from_row(original.to_row())
    assert rebuilt == original


def test_roundtrip_preserves_optional_nones():
    """``theme``, ``chunk_id``, ``speaker`` survive a None round-trip."""
    original = _make_event(
        theme=None,
        chunk_id=None,
        speaker=None,
        source="polygon",
        provider_id="polygon-article-99",
    )
    rebuilt = NarrativeEvent.from_row(original.to_row())
    assert rebuilt == original
    assert rebuilt.theme is None
    assert rebuilt.chunk_id is None
    assert rebuilt.speaker is None


def test_roundtrip_preserves_timezone_aware_utc():
    """Datetimes survive the ISO-8601 string round-trip with tz intact."""
    rebuilt = NarrativeEvent.from_row(_make_event().to_row())
    assert rebuilt.published_utc.tzinfo is not None
    assert rebuilt.ingested_utc.tzinfo is not None
    assert rebuilt.published_utc == datetime(
        2026, 5, 1, 14, 30, tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------- #
# Deterministic ID                                                       #
# ---------------------------------------------------------------------- #


def test_identical_identity_tuple_produces_identical_ids():
    """Two events with the same (source, provider_id, chunk_id, symbol)
    share an ID — that's what makes ``INSERT OR IGNORE`` idempotent."""
    e1 = _make_event(polarity=0.5, text_excerpt="first wording")
    e2 = _make_event(polarity=-0.1, text_excerpt="totally different wording")
    assert e1.id == e2.id


def test_different_symbol_produces_different_id():
    """Changing only the symbol must change the ID."""
    e1 = _make_event(symbol="NVDA")
    e2 = _make_event(symbol="AMD")
    assert e1.id != e2.id


def test_different_source_produces_different_id():
    """A Polygon event about NVDA and a transcript event about NVDA from
    the same provider_id must not collide — they're different rows."""
    e1 = _make_event(source="polygon", chunk_id=None)
    e2 = _make_event(source="youtube_transcript", chunk_id=None)
    assert e1.id != e2.id


def test_id_is_32_hex_chars():
    """Sanity: the ID format is the documented sha256 prefix."""
    eid = _make_event().id
    assert len(eid) == 32
    assert all(c in "0123456789abcdef" for c in eid)


def test_create_uppercases_symbol_and_uses_it_in_id():
    """``create`` uppercases the symbol before hashing, so callers can
    pass lowercase without producing a second copy of the row."""
    upper = _make_event(symbol="NVDA")
    lower = _make_event(symbol="nvda")
    assert upper.id == lower.id
    assert lower.symbol == "NVDA"


# ---------------------------------------------------------------------- #
# Polarity validation                                                    #
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_polarity", [1.5, -1.5, 2.0, -10.0])
def test_polarity_out_of_range_raises(bad_polarity):
    """Out-of-range polarity raises ``ValueError`` rather than clamping.

    Documented in :class:`NarrativeEvent`: a polarity outside
    ``[-1.0, +1.0]`` is a producer bug; failing loud at the write
    boundary surfaces the underlying defect.
    """
    with pytest.raises(ValueError, match="polarity"):
        _make_event(polarity=bad_polarity)


@pytest.mark.parametrize("ok_polarity", [-1.0, -0.5, 0.0, 0.5, 1.0])
def test_polarity_at_boundary_is_accepted(ok_polarity):
    """The endpoints ``-1.0`` and ``+1.0`` are valid."""
    event = _make_event(polarity=ok_polarity)
    assert event.polarity == ok_polarity


# ---------------------------------------------------------------------- #
# Schema constant sanity                                                 #
# ---------------------------------------------------------------------- #


def test_schema_sql_declares_events_table_and_indexes():
    """Cheap guard against an accidental edit that drops the table or
    one of the load-bearing indexes the Phase 4 spec requires."""
    sql = EVENTS_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS events" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_events_symbol_time" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_events_theme_time" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_events_source_time" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_events_time" in sql
    assert "CREATE TABLE IF NOT EXISTS _meta" in sql
