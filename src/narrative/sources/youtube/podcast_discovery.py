"""Podcast RSS discovery: the Phase 3 alternative to YouTube paths.

Most of the watched interview shows publish via standard podcast hosts
(Megaphone, Transistor, Libsyn, Simplecast, Substack) whose audio URLs
are not IP-gated the way YouTube is. Discovery here matches the same
speaker rules as the YouTube layer but emits ``VideoCandidate``s with
``source="podcast"`` and a populated ``audio_url`` so the fetcher can
pull the MP3 directly.

The match logic is intentionally simpler than YouTube's:
  - No shorts filter (podcasts don't have them).
  - No owned/show split (every podcast-feed channel is treated as a
    show-channel for matching: a speaker hit is required).
  - No event-keyword rule (earnings/keynotes live on company YouTube
    channels, not in podcast feeds).
  - A ``lookback_days`` cap on entries — podcast feeds often carry
    hundreds of episodes; we only care about recent ones.

Description matching is run against the first 1500 chars of the
description because podcast show notes commonly start with the actual
episode summary and then drift into ad reads, sponsor lists, and
"support the show" boilerplate. The full text is preserved on the
candidate for future use.
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import feedparser

from .discovery import (
    ChannelSpec,
    DiscoveryConfig,
    SpeakerSpec,
    VideoCandidate,
    _all_speaker_matches,
    _build_speaker_regex_map,
    _published_utc,
)

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; ScannerBot/0.1; +intelligent-stock-scanner)"

# Where to stop scanning the description for speaker matches.
_DESC_SCAN_LIMIT = 1500


# --------------------------------------------------------------------- #
# Public API                                                            #
# --------------------------------------------------------------------- #


def discover_podcast_candidates(
    config: DiscoveryConfig,
    *,
    feed_loader: Callable[[str], feedparser.FeedParserDict] | None = None,
    max_workers: int = 8,
    lookback_days: int = 30,
    filter_to_speaker_tiers: frozenset[int] | None = None,
) -> list[VideoCandidate]:
    """Fetch every channel with a non-null ``podcast_rss`` and match.

    ``feed_loader`` takes the RSS URL (not a channel_id, unlike the
    YouTube discoverer's loader). Production code leaves it ``None`` and
    pays one HTTP GET per feed.

    ``filter_to_speaker_tiers`` (Phase 3.5) optionally narrows the
    result to candidates whose primary ``matched_speaker`` is in the
    given tier set. ``None`` (the default) means no filtering — Phase 3
    live ingestion uses ``None``; backfill passes ``frozenset({1})`` to
    keep only tier-1 speakers.
    """
    loader = feed_loader or _fetch_podcast_feed
    speaker_regex = _build_speaker_regex_map(config.speakers)
    speakers_by_id = {sp.speaker_id: sp for sp in config.speakers}

    actionable = [c for c in config.channels if c.podcast_rss]
    out: list[VideoCandidate] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _worker, ch, loader, speaker_regex, speakers_by_id, lookback_days
            ): ch
            for ch in actionable
        }
        for fut in as_completed(futures):
            ch = futures[fut]
            try:
                out.extend(fut.result())
            except Exception as exc:  # pragma: no cover - worker already swallows
                log.warning(
                    "podcast worker raised for %s (%s): %s",
                    ch.name, ch.podcast_rss, exc,
                )

    if filter_to_speaker_tiers is not None:
        out = [
            c for c in out
            if c.matched_speaker is not None
            and c.matched_speaker in speakers_by_id
            and speakers_by_id[c.matched_speaker].tier in filter_to_speaker_tiers
        ]

    out.sort(key=lambda c: (-c.published_utc.timestamp(), c.channel, c.video_id))
    return out


def match_podcast_entries(
    channel: ChannelSpec,
    feed: feedparser.FeedParserDict,
    speaker_regex: dict[str, re.Pattern[str]],
    speakers: dict[str, SpeakerSpec],
    *,
    lookback_days: int = 30,
) -> list[VideoCandidate]:
    """Apply the podcast match rules. Pure function over a parsed feed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    pseudo_channel_id = _pseudo_channel_id(channel)
    out: list[VideoCandidate] = []
    for entry in feed.entries:
        published = _published_utc(entry)
        if published < cutoff:
            continue
        cand = _match_one(entry, channel, pseudo_channel_id, published,
                          speaker_regex, speakers)
        if cand is not None:
            out.append(cand)
    return out


# --------------------------------------------------------------------- #
# Per-entry matcher                                                     #
# --------------------------------------------------------------------- #


def _match_one(
    entry: Any,
    channel: ChannelSpec,
    pseudo_channel_id: str,
    published: datetime,
    speaker_regex: dict[str, re.Pattern[str]],
    speakers: dict[str, SpeakerSpec],
) -> VideoCandidate | None:
    title = (entry.get("title") or "").strip()
    full_desc = _podcast_description(entry)
    scan_desc = full_desc[:_DESC_SCAN_LIMIT]

    matches = _all_speaker_matches(title, scan_desc, speaker_regex, speakers)
    if not matches:
        return None
    sid, source, matched_text = matches[0]
    sp = speakers[sid]
    co = tuple(m[0] for m in matches[1:])

    guid = entry.get("id") or entry.get("guid") or ""
    if not guid:
        # No stable identifier — skip; we won't be able to dedupe in the
        # store on re-runs.
        return None

    audio_url = _enclosure_url(entry)
    web_url = (entry.get("link") or "") or (audio_url or "")

    return VideoCandidate(
        channel=channel.name,
        channel_id=pseudo_channel_id,
        video_id=_pc_id(guid),
        url=web_url,
        title=title,
        published_utc=published,
        reason=f"speaker:{sid}",
        matched_speaker=sid,
        matched_tickers=sp.tickers + sp.amplifies,
        matched_text=matched_text,
        match_source=source,
        co_speakers=co,
        source="podcast",
        audio_url=audio_url,
        episode_guid=str(guid),
    )


# --------------------------------------------------------------------- #
# Entry projection                                                      #
# --------------------------------------------------------------------- #


def _podcast_description(entry: Any) -> str:
    """Concatenate every distinct description-ish field in podcast feeds."""
    parts: list[str] = []
    seen: set[str] = set()
    for key in ("summary", "subtitle", "description"):
        val = entry.get(key, "") or ""
        if isinstance(val, str) and val and val not in seen:
            parts.append(val)
            seen.add(val)
    content = entry.get("content")
    if isinstance(content, list):
        for item in content:
            val = item.get("value", "") if isinstance(item, dict) else ""
            if isinstance(val, str) and val and val not in seen:
                parts.append(val)
                seen.add(val)
    return "\n".join(parts)


def _enclosure_url(entry: Any) -> str | None:
    """Find the audio enclosure URL for a podcast entry.

    Prefers audio/* MIME types; falls back to the first enclosure.
    """
    enclosures = entry.get("enclosures") or []
    for enc in enclosures:
        if not isinstance(enc, dict):
            continue
        href = enc.get("href") or enc.get("url")
        mime = (enc.get("type") or "").lower()
        if href and mime.startswith("audio"):
            return href
    for enc in enclosures:
        if isinstance(enc, dict):
            href = enc.get("href") or enc.get("url")
            if href:
                return href
    return None


def _pc_id(guid: str) -> str:
    """Synthetic stable episode_id from the RSS guid.

    11 hex chars of sha1, prefixed with ``pc_`` → 14 chars total. The
    ``pc_`` prefix guarantees no collision with YouTube's 11-char IDs.
    """
    h = hashlib.sha1(guid.encode("utf-8")).hexdigest()
    return f"pc_{h[:11]}"


def _pseudo_channel_id(channel: ChannelSpec) -> str:
    """Synthetic channel_id for podcast rows.

    Podcasts don't carry a YouTube ``UC...`` ID. We need something
    non-null and identifiable so the episodes table's ``channel_id``
    column stays sane and queries against it work.
    """
    return f"podcast:{channel.name.lower().replace(' ', '_')}"


# --------------------------------------------------------------------- #
# RSS fetching                                                          #
# --------------------------------------------------------------------- #


def _worker(
    channel: ChannelSpec,
    loader: Callable[[str], feedparser.FeedParserDict],
    speaker_regex: dict[str, re.Pattern[str]],
    speakers: dict[str, SpeakerSpec],
    lookback_days: int,
) -> list[VideoCandidate]:
    assert channel.podcast_rss is not None  # actionable filter guarantees this
    try:
        feed = loader(channel.podcast_rss)
    except Exception as exc:
        log.warning(
            "podcast feed fetch failed for %s (%s): %s",
            channel.name, channel.podcast_rss, exc,
        )
        return []
    return match_podcast_entries(
        channel, feed, speaker_regex, speakers, lookback_days=lookback_days,
    )


def _fetch_podcast_feed(url: str, *, timeout: int = 30) -> feedparser.FeedParserDict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return feedparser.parse(r.read())
