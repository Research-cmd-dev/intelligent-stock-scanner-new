"""Tests for the YouTube discovery layer (Phase 2).

All tests are hermetic: the production ``_fetch_feed`` HTTP call is bypassed
via the ``feed_loader`` parameter on :func:`discover_candidates`, and
per-entry tests call :func:`match_entries` directly on a parsed fixture.

Fixtures live under ``tests/fixtures/rss/`` as real-shaped YouTube Atom XML
so behavior stays anchored to the actual upstream format.
"""

from __future__ import annotations

import logging
from pathlib import Path

import feedparser
import pytest

from src.narrative.sources.youtube.discovery import (
    ChannelSpec,
    DiscoveryConfig,
    SpeakerSpec,
    VideoCandidate,
    _build_speaker_regex_map,
    discover_candidates,
    load_config,
    match_entries,
)


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "rss"


def _load_fixture(name: str) -> feedparser.FeedParserDict:
    return feedparser.parse((_FIXTURE_DIR / name).read_bytes())


# --------------------------------------------------------------------- #
# Shared fixtures                                                       #
# --------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def config() -> DiscoveryConfig:
    return load_config()


@pytest.fixture(scope="module")
def speakers(config: DiscoveryConfig) -> dict[str, SpeakerSpec]:
    return {sp.speaker_id: sp for sp in config.speakers}


@pytest.fixture(scope="module")
def regex_map(config: DiscoveryConfig):
    return _build_speaker_regex_map(config.speakers)


def _lex() -> ChannelSpec:
    return ChannelSpec(
        name="Lex Fridman Podcast",
        channel_id="UCSHZKyawb77ixDdsGog4iWA",
        handle="https://www.youtube.com/@lexfridman",
        owned=False,
        type="tech_interview",
    )


def _bg2() -> ChannelSpec:
    return ChannelSpec(
        name="Bg2 Pod",
        channel_id="UC-yRDvpR99LUc5l7i7jLzew",
        handle="https://www.youtube.com/@Bg2Pod",
        owned=False,
        type="tech_interview",
    )


def _twentyvc() -> ChannelSpec:
    return ChannelSpec(
        name="20VC",
        channel_id="UCf0PBRjhf0rF8fWBIxTuoWA",
        handle="https://www.youtube.com/@20VC",
        owned=False,
        type="tech_interview",
    )


def _tesla() -> ChannelSpec:
    return ChannelSpec(
        name="Tesla",
        channel_id="UC5WjFrtBdufl6CZojX3D8dQ",
        handle="https://www.youtube.com/@tesla",
        owned=True,
        type="company",
    )


# --------------------------------------------------------------------- #
# 1. Title match on a show channel                                      #
# --------------------------------------------------------------------- #


def test_title_match_speaker(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    feed = _load_fixture("lex_jensen_title.xml")
    candidates = match_entries(_lex(), feed, regex_map, speakers)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.reason == "speaker:jensen_huang"
    assert c.matched_speaker == "jensen_huang"
    assert c.match_source == "title"
    assert c.matched_text.startswith("[TITLE]")
    assert "NVDA" in c.matched_tickers
    assert c.video_id == "vif8NQcjVf0"
    assert c.url == "https://www.youtube.com/watch?v=vif8NQcjVf0"
    assert c.published_utc.tzinfo is not None
    assert c.channel == "Lex Fridman Podcast"


# --------------------------------------------------------------------- #
# 2. Description match with guest cue                                   #
# --------------------------------------------------------------------- #


def test_description_match_with_cue(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    """'Brad Gerstner sits down with Satya Nadella' — guest (Satya) wins
    over host (Brad) because his name appears AFTER the cue."""
    feed = _load_fixture("bg2_satya_desc.xml")
    candidates = match_entries(_bg2(), feed, regex_map, speakers)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.reason == "speaker:satya_nadella"
    assert c.matched_speaker == "satya_nadella"
    assert c.match_source == "description"
    assert c.matched_text.startswith("[DESC]")
    assert "sits down with" in c.matched_text or "Satya" in c.matched_text


# --------------------------------------------------------------------- #
# 3. Chapter-marker false positive must be rejected                     #
# --------------------------------------------------------------------- #


def test_chapter_marker_false_positive(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    """'Elon Musk' in a chapter marker, far from any guest cue → reject."""
    feed = _load_fixture("chapter_marker_false_positive.xml")
    candidates = match_entries(_twentyvc(), feed, regex_map, speakers)
    assert candidates == []


# --------------------------------------------------------------------- #
# 4. Shorts URL rejected before any other rule fires                    #
# --------------------------------------------------------------------- #


def test_shorts_rejected(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    feed = _load_fixture("shorts_negative.xml")
    candidates = match_entries(_tesla(), feed, regex_map, speakers)
    assert candidates == []


# --------------------------------------------------------------------- #
# 5. Owned channel + event keyword                                      #
# --------------------------------------------------------------------- #


def test_owned_event_keyword(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    feed = _load_fixture("tesla_earnings.xml")
    candidates = match_entries(_tesla(), feed, regex_map, speakers)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.reason == "owned+event"
    assert c.matched_speaker is None
    assert c.matched_tickers == ()
    assert c.match_source == "owned"
    # Matched substring should be either the quarter token or the
    # 'financial results' phrase — both are in EVENT_RE.
    matched_low = c.matched_text.lower()
    assert "q1" in matched_low or "financial results" in matched_low


# --------------------------------------------------------------------- #
# 6. Owned channel marketing clip with no event keyword → reject        #
# --------------------------------------------------------------------- #


def test_owned_marketing_rejected(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    feed = _load_fixture("tesla_marketing.xml")
    candidates = match_entries(_tesla(), feed, regex_map, speakers)
    assert candidates == []


# --------------------------------------------------------------------- #
# 7. Ambiguous speaker without context → reject                         #
# --------------------------------------------------------------------- #


def test_ambiguous_speaker_requires_context(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    feed = _load_fixture("ambiguous_no_context.xml")
    candidates = match_entries(_lex(), feed, regex_map, speakers)
    assert candidates == []


# --------------------------------------------------------------------- #
# 8. Ambiguous speaker with context cue → accept                        #
# --------------------------------------------------------------------- #


def test_ambiguous_speaker_with_context(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    feed = _load_fixture("ambiguous_with_context.xml")
    candidates = match_entries(_lex(), feed, regex_map, speakers)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.matched_speaker == "tom_lee"
    assert c.reason == "speaker:tom_lee"


# --------------------------------------------------------------------- #
# 9. One failing feed must not poison the result                        #
# --------------------------------------------------------------------- #


def test_partial_failure_isolation(
    speakers: dict[str, SpeakerSpec],
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = DiscoveryConfig(
        channels=(_lex(), _bg2()),
        speakers=tuple(speakers.values()),
    )

    def loader(channel_id: str) -> feedparser.FeedParserDict:
        if channel_id == _bg2().channel_id:
            raise RuntimeError("simulated network failure")
        return _load_fixture("lex_jensen_title.xml")

    with caplog.at_level(logging.WARNING, logger="src.narrative.sources.youtube.discovery"):
        candidates = discover_candidates(config, feed_loader=loader, max_workers=2)

    assert len(candidates) == 1
    assert candidates[0].channel == "Lex Fridman Podcast"
    assert any("Bg2 Pod" in r.message for r in caplog.records)


# --------------------------------------------------------------------- #
# 10. Determinism (acceptance criterion 5)                              #
# --------------------------------------------------------------------- #


# --------------------------------------------------------------------- #
# 11. Co-speakers: multiple watched speakers, one primary, rest in co_  #
# --------------------------------------------------------------------- #


def test_co_speakers(
    speakers: dict[str, SpeakerSpec], regex_map
) -> None:
    """When two+ watched speakers co-occur with guest cues, one becomes the
    primary ``matched_speaker`` and the others land in ``co_speakers``."""
    feed = _load_fixture("co_speakers_test.xml")
    candidates = match_entries(_bg2(), feed, regex_map, speakers)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.source == "youtube"  # default
    assert c.audio_url is None
    assert c.episode_guid is None
    # Sam Altman and Elon Musk are both guests; whichever is primary, the
    # other must appear in co_speakers.
    primary_and_co = {c.matched_speaker, *c.co_speakers}
    assert {"sam_altman", "elon_musk"}.issubset(primary_and_co)
    # Brad Gerstner is the host — he's also on the watchlist so he should
    # show up in co_speakers (host-flagged, but still emitted).
    assert "brad_gerstner" in c.co_speakers


# --------------------------------------------------------------------- #
# 12. Determinism (acceptance criterion 5)                              #
# --------------------------------------------------------------------- #


def test_discover_candidates_deterministic(
    speakers: dict[str, SpeakerSpec],
) -> None:
    config = DiscoveryConfig(
        channels=(_lex(), _tesla()),
        speakers=tuple(speakers.values()),
    )

    def loader(channel_id: str) -> feedparser.FeedParserDict:
        if channel_id == _tesla().channel_id:
            return _load_fixture("tesla_earnings.xml")
        return _load_fixture("lex_jensen_title.xml")

    out1 = discover_candidates(config, feed_loader=loader, max_workers=2)
    out2 = discover_candidates(config, feed_loader=loader, max_workers=2)
    # frozen+slots dataclasses give structural equality
    assert out1 == out2
    assert all(isinstance(c, VideoCandidate) for c in out1)
    # Sort invariant: published_utc descending
    timestamps = [c.published_utc for c in out1]
    assert timestamps == sorted(timestamps, reverse=True)
