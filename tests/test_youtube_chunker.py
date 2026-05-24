"""Tests for the pure-function transcript rechunker."""

from __future__ import annotations

import pytest

from src.narrative.sources.youtube.chunker import Chunk, Segment, rechunk


def _seg(start: float, end: float, text: str) -> Segment:
    return Segment(start_s=start, end_s=end, text=text)


# --------------------------------------------------------------------- #
# 1. Empty input                                                        #
# --------------------------------------------------------------------- #


def test_empty_input_returns_empty() -> None:
    assert rechunk([]) == []


# --------------------------------------------------------------------- #
# 2. Single short segment                                                #
# --------------------------------------------------------------------- #


def test_single_short_segment_kept() -> None:
    """A single segment shorter than target_s collapses into one chunk,
    even if its text is below min_chunk_chars — otherwise short episodes
    would produce zero output."""
    text = "Short clip about NVDA earnings."  # 31 chars, well under default 200
    chunks = rechunk([_seg(0.0, 12.0, text)], target_s=60.0)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.idx == 0
    assert c.text == text
    assert c.start_s == 0.0
    assert c.end_s == 12.0


# --------------------------------------------------------------------- #
# 3. Target compliance: most chunks land near target_s                   #
# --------------------------------------------------------------------- #


def test_target_compliance_long_input() -> None:
    """Long, evenly-spaced input should produce roughly N = duration/step
    chunks, each roughly target_s long. We assert: at least 3 chunks, and
    every chunk's *span* is bounded by 2*target_s (no runaway windows)."""
    # 240 seconds of segments, one every 3s
    segs = [_seg(t, t + 3.0, f"segment at {t}s with enough text to count.") for t in range(0, 240, 3)]
    chunks = rechunk(segs, target_s=60.0, overlap_s=5.0)
    assert len(chunks) >= 3
    for c in chunks:
        assert c.end_s - c.start_s <= 2 * 60.0
    # Indices must be 0, 1, 2, ... with no gaps
    assert [c.idx for c in chunks] == list(range(len(chunks)))


# --------------------------------------------------------------------- #
# 4. Overlap correctness: adjacent chunks share segments                 #
# --------------------------------------------------------------------- #


def test_overlap_creates_shared_coverage() -> None:
    """With step = 55s and target = 60s, chunk N's window ends at t+60
    and chunk N+1's window starts at t+55 — a segment at midpoint 57s
    lands in both."""
    # 200s of segments; pad each segment's text so it's not dropped
    pad = " padding text " * 5
    segs = [_seg(t, t + 2.0, f"chunk-marker-{int(t)}{pad}") for t in range(0, 200, 2)]
    chunks = rechunk(segs, target_s=60.0, overlap_s=5.0)
    assert len(chunks) >= 2
    # Adjacent chunks should overlap in time: chunk[i].end_s > chunk[i+1].start_s
    for a, b in zip(chunks, chunks[1:]):
        assert b.start_s < a.end_s, f"no overlap between chunk {a.idx} and {b.idx}"


# --------------------------------------------------------------------- #
# 5. Tiny final-fragment drop                                            #
# --------------------------------------------------------------------- #


def test_tiny_trailing_fragment_dropped() -> None:
    """A 65-second podcast with a 60s real-content chunk and a 5s
    music-tail fragment should drop the fragment."""
    body_text = ("This is a substantial paragraph of speech that more "
                 "than meets the minimum chunk character threshold. ") * 5
    segs = [
        _seg(0.0, 55.0, body_text),
        _seg(60.0, 65.0, "outro music"),  # 11 chars — way under min
    ]
    chunks = rechunk(segs, target_s=60.0, overlap_s=5.0, min_chunk_chars=200)
    # 'outro music' fragment should be dropped
    assert all("outro music" not in c.text for c in chunks), [c.text for c in chunks]


def test_tiny_only_chunk_is_preserved() -> None:
    """If the whole episode is shorter than min_chunk_chars, we still
    emit one chunk — otherwise we'd silently lose short content."""
    segs = [_seg(0.0, 5.0, "tiny")]
    chunks = rechunk(segs, target_s=60.0, overlap_s=5.0, min_chunk_chars=200)
    assert len(chunks) == 1
    assert chunks[0].text == "tiny"


# --------------------------------------------------------------------- #
# 6. Determinism                                                         #
# --------------------------------------------------------------------- #


def test_rechunk_deterministic() -> None:
    segs = [_seg(t, t + 3.0, f"deterministic text content at second {t}.") for t in range(0, 180, 3)]
    a = rechunk(segs, target_s=60.0, overlap_s=5.0)
    b = rechunk(segs, target_s=60.0, overlap_s=5.0)
    assert a == b


# --------------------------------------------------------------------- #
# Bonus: param-validation                                                #
# --------------------------------------------------------------------- #


def test_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        rechunk([_seg(0, 1, "x")], target_s=0)
    with pytest.raises(ValueError):
        rechunk([_seg(0, 1, "x")], target_s=60, overlap_s=60)
    with pytest.raises(ValueError):
        rechunk([_seg(0, 1, "x")], target_s=60, overlap_s=-1)
