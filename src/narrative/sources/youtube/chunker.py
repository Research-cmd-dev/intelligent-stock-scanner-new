"""Pure-function rechunker for transcript segments.

Phase 3 fetchers emit lots of small :class:`Segment`s (one per Whisper
output or VTT cue — typically 2–6 seconds each). The narrative pipeline
prefers windows around 60 seconds with a small overlap so a thought
that straddles a window boundary still gets captured in both chunks.

Owns both :class:`Segment` and :class:`Chunk` so the heavy fetcher
module (which lazy-imports Whisper, yt-dlp, etc.) doesn't have to be
the type owner. Anything downstream — store, event emitter — imports
the types from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Segment:
    """One raw transcript line (a Whisper segment or a single VTT cue)."""

    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True, slots=True)
class Chunk:
    """One ~60s window of speech, rechunked from many :class:`Segment`s."""

    idx: int
    start_s: float
    end_s: float
    text: str


def rechunk(
    segments: Sequence[Segment],
    *,
    target_s: float = 60.0,
    overlap_s: float = 5.0,
    min_chunk_chars: int = 200,
) -> list[Chunk]:
    """Group ``segments`` into overlapping ~``target_s`` windows.

    Algorithm:

    1. Step forward by ``target_s - overlap_s`` seconds.
    2. At each window ``[t, t+target_s)`` collect every segment whose
       *midpoint* falls inside the window. Midpoint membership keeps a
       given segment in exactly one or two adjacent windows; never three.
    3. The chunk's ``start_s`` / ``end_s`` are the first/last member
       segment's bounds (so the chunk's range reflects what was actually
       included, not the abstract window).
    4. The trailing chunk is dropped if it's shorter than
       ``min_chunk_chars`` AND there's at least one earlier chunk —
       protects against tiny "music tail" fragments without making a
       short episode produce zero output.

    Pure function. Deterministic. Raises ``ValueError`` on bad params.
    """
    if not segments:
        return []
    if target_s <= 0:
        raise ValueError(f"target_s must be > 0, got {target_s}")
    if overlap_s < 0 or overlap_s >= target_s:
        raise ValueError(f"overlap_s must be in [0, {target_s}), got {overlap_s}")

    step = target_s - overlap_s
    last_end = max(s.end_s for s in segments)
    out: list[Chunk] = []
    idx = 0
    t = 0.0

    while t < last_end:
        win_lo = t
        win_hi = t + target_s
        members = [s for s in segments if win_lo <= (s.start_s + s.end_s) / 2.0 < win_hi]
        if members:
            text = " ".join(s.text.strip() for s in members if s.text.strip()).strip()
            if text:
                out.append(
                    Chunk(
                        idx=idx,
                        start_s=members[0].start_s,
                        end_s=members[-1].end_s,
                        text=text,
                    )
                )
                idx += 1
        t += step

    if len(out) > 1 and len(out[-1].text) < min_chunk_chars:
        out = out[:-1]
    return out
