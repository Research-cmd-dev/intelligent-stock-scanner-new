"""Tests for the podcast RSS discovery layer."""

from __future__ import annotations

from pathlib import Path

import feedparser
import pytest

from src.narrative.sources.youtube.discovery import (
    ChannelSpec,
    DiscoveryConfig,
    SpeakerSpec,
    _build_speaker_regex_map,
    load_config,
)
from src.narrative.sources.youtube.podcast_discovery import (
    _pc_id,
    _pseudo_channel_id,
    discover_podcast_candidates,
    match_podcast_entries,
)


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "podcasts"
# A lookback so wide the positive tests don't drift even if run far in the
# future. The lookback-rejection test uses the default (30) explicitly.
_WIDE_LOOKBACK = 10_000


def _load(name: str) -> feedparser.FeedParserDict:
    return feedparser.parse((_FIXTURE_DIR / name).read_bytes())


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
        podcast_rss="https://lexfridman.com/feed/podcast/",
    )


def _bg2() -> ChannelSpec:
    return ChannelSpec(
        name="Bg2 Pod",
        channel_id="UC-yRDvpR99LUc5l7i7jLzew",
        handle="https://www.youtube.com/@Bg2Pod",
        owned=False,
        type="tech_interview",
        podcast_rss="https://feeds.example/bg2pod",  # synthetic
    )


def _acquired() -> ChannelSpec:
    return ChannelSpec(
        name="Acquired",
        channel_id="UCyFqFYfTW2VoIQKylJ04Rtw",
        handle="https://www.youtube.com/@AcquiredFM",
        owned=False,
        type="tech_interview",
        podcast_rss="https://feeds.transistor.fm/acquired",
    )


# --------------------------------------------------------------------- #
# 1. Title-match podcast episode → accept                               #
# --------------------------------------------------------------------- #


def test_title_match(speakers, regex_map) -> None:
    feed = _load("lex_jensen_episode.xml")
    cands = match_podcast_entries(_lex(), feed, regex_map, speakers, lookback_days=_WIDE_LOOKBACK)
    assert len(cands) == 1
    c = cands[0]
    assert c.source == "podcast"
    assert c.matched_speaker == "jensen_huang"
    assert c.reason == "speaker:jensen_huang"
    assert c.match_source == "title"
    assert c.channel_id == "podcast:lex_fridman_podcast"
    assert c.episode_guid == "lexfridman-ep-494"


# --------------------------------------------------------------------- #
# 2. Description-match with guest cue → accept                          #
# --------------------------------------------------------------------- #


def test_description_match(speakers, regex_map) -> None:
    feed = _load("bg2_satya_episode.xml")
    cands = match_podcast_entries(_bg2(), feed, regex_map, speakers, lookback_days=_WIDE_LOOKBACK)
    assert len(cands) == 1
    c = cands[0]
    assert c.matched_speaker == "satya_nadella"
    assert c.match_source == "description"
    # Brad Gerstner is the host but also a watched speaker → co_speakers
    assert "brad_gerstner" in c.co_speakers


# --------------------------------------------------------------------- #
# 3. audio_url + video_id extracted from enclosure                      #
# --------------------------------------------------------------------- #


def test_audio_url_extracted(speakers, regex_map) -> None:
    feed = _load("lex_jensen_episode.xml")
    cands = match_podcast_entries(_lex(), feed, regex_map, speakers, lookback_days=_WIDE_LOOKBACK)
    c = cands[0]
    assert c.audio_url == "https://media.lexfridman.com/podcast/494.mp3"
    # Synthetic video_id has the pc_ prefix and total length 14
    assert c.video_id.startswith("pc_")
    assert len(c.video_id) == 14
    # Stable hash: re-deriving from the guid must match
    assert c.video_id == _pc_id("lexfridman-ep-494")


# --------------------------------------------------------------------- #
# 4. Episode outside lookback window → reject                           #
# --------------------------------------------------------------------- #


def test_lookback_rejects_old(speakers, regex_map) -> None:
    feed = _load("old_episode_outside_window.xml")
    # Default lookback_days=30 — the 2024-01-15 episode is way past that.
    cands = match_podcast_entries(_lex(), feed, regex_map, speakers)
    assert cands == []


# --------------------------------------------------------------------- #
# 5. Episode with no watched speakers → reject                          #
# --------------------------------------------------------------------- #


def test_no_watched_speaker_rejected(speakers, regex_map) -> None:
    feed = _load("acquired_company_episode.xml")
    cands = match_podcast_entries(_acquired(), feed, regex_map, speakers, lookback_days=_WIDE_LOOKBACK)
    assert cands == []


# --------------------------------------------------------------------- #
# 6. Orchestrator: only channels with podcast_rss are queried           #
# --------------------------------------------------------------------- #


def test_discover_only_actionable_channels(speakers, regex_map) -> None:
    """A channel without ``podcast_rss`` must not be passed to the loader."""
    seen_urls: list[str] = []

    def loader(url: str) -> feedparser.FeedParserDict:
        seen_urls.append(url)
        return _load("lex_jensen_episode.xml")

    config = DiscoveryConfig(
        channels=(
            _lex(),                                           # has podcast_rss
            ChannelSpec("NoFeed", "UCnoooooooooooooooooooo",  # no podcast_rss
                        "", False, "tech_interview"),
        ),
        speakers=tuple(speakers.values()),
    )
    cands = discover_podcast_candidates(
        config, feed_loader=loader, max_workers=2, lookback_days=_WIDE_LOOKBACK,
    )
    assert seen_urls == ["https://lexfridman.com/feed/podcast/"]
    assert len(cands) == 1


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


def test_pseudo_channel_id_format() -> None:
    assert _pseudo_channel_id(_lex()) == "podcast:lex_fridman_podcast"


# --------------------------------------------------------------------- #
# Phase 3.5: lookback + tier filter                                     #
# --------------------------------------------------------------------- #


def _macro_voices() -> ChannelSpec:
    return ChannelSpec(
        name="Macro Voices",
        channel_id="UCPQsZQ2yvJFv0TCo2wqZMgg",
        handle="https://www.youtube.com/@macrovoices",
        owned=False,
        type="macro",
        podcast_rss="https://feeds.example/macrovoices",  # synthetic
    )


def test_lookback_long_window(speakers, regex_map) -> None:
    """Three episodes spanning ~2.5 years: wide lookback admits all,
    narrow lookback admits a strict subset (don't pin the exact count
    since 'now' drifts over the lifetime of the repo)."""
    from src.narrative.sources.youtube.podcast_discovery import match_podcast_entries
    feed = _load("historical_long_window.xml")
    wide = match_podcast_entries(_lex(), feed, regex_map, speakers, lookback_days=10000)
    narrow = match_podcast_entries(_lex(), feed, regex_map, speakers, lookback_days=30)
    assert len(wide) == 3
    assert len(narrow) < len(wide)


def test_tier_filter_keeps_tier1(speakers, regex_map) -> None:
    """One tier-1 (Jensen) feed + one tier-4 (Druckenmiller) feed:
    no filter keeps both; tier={1} keeps only Jensen."""
    config = DiscoveryConfig(
        channels=(_lex(), _macro_voices()),
        speakers=tuple(speakers.values()),
    )

    def loader(url: str) -> feedparser.FeedParserDict:
        if url == _lex().podcast_rss:
            return _load("lex_jensen_episode.xml")
        return _load("tier4_macro_episode.xml")

    no_filter = discover_podcast_candidates(
        config, feed_loader=loader, max_workers=2, lookback_days=_WIDE_LOOKBACK,
    )
    assert len(no_filter) == 2

    tier1 = discover_podcast_candidates(
        config, feed_loader=loader, max_workers=2, lookback_days=_WIDE_LOOKBACK,
        filter_to_speaker_tiers=frozenset({1}),
    )
    assert len(tier1) == 1
    assert tier1[0].matched_speaker == "jensen_huang"


def test_tier_filter_empty_set_admits_nothing(speakers, regex_map) -> None:
    config = DiscoveryConfig(
        channels=(_lex(),),
        speakers=tuple(speakers.values()),
    )

    def loader(url: str) -> feedparser.FeedParserDict:
        return _load("lex_jensen_episode.xml")

    cands = discover_podcast_candidates(
        config, feed_loader=loader, max_workers=1, lookback_days=_WIDE_LOOKBACK,
        filter_to_speaker_tiers=frozenset(),
    )
    assert cands == []
