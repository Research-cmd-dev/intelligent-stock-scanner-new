"""Briefing storage helpers — Markdown file I/O to the Modal volume.

DB ops (``upsert_briefing`` / ``get_briefing`` / ``list_recent_briefings``)
live on :class:`TranscriptStore` for symmetry with the rest of the Phase 3
schema. This module owns the Markdown sidecar files: it writes the
``/data/briefings/YYYY-MM-DD.md`` companion to the DB row so the briefing
is also readable as a plain file on the volume.
"""

from __future__ import annotations

from datetime import date as date_
from pathlib import Path


DEFAULT_BRIEFINGS_DIR = Path("/data/briefings")


def write_markdown(
    markdown: str,
    *,
    briefing_date: date_,
    briefings_dir: Path = DEFAULT_BRIEFINGS_DIR,
) -> Path:
    """Write ``<date>.md`` to the briefings dir, creating it if needed.

    Returns the written path. Overwrites any prior file for the same
    date — same idempotency contract as the DB row.
    """
    briefings_dir.mkdir(parents=True, exist_ok=True)
    out = briefings_dir / f"{briefing_date.isoformat()}.md"
    out.write_text(markdown, encoding="utf-8")
    return out


def briefing_id_for(briefing_date: date_) -> str:
    """Canonical primary key for the briefings table."""
    return f"briefing_{briefing_date.isoformat()}"
