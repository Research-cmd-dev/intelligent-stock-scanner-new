"""YouTube discovery layer.

Fetches each watched channel's public Atom feed
(``/feeds/videos.xml?channel_id=<UC...>``), applies the v2 match rules to
title + description, and returns a list of :class:`VideoCandidate`
records for downstream transcript ingestion.

This module does **not** download transcripts, score sentiment, or write
to ``narrative_events`` — those are Phase 3+ concerns. Discovery is a
pure read-only filter over RSS feeds.

The module is intentionally not registered in :func:`default_sources` —
YouTube items flow into the narrative pipeline via ``narrative_events``
(Phase 4), not via the ``NewsItem`` fetch API.
"""

from __future__ import annotations

import logging
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import feedparser
import yaml

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# Match rule constants                                                  #
# --------------------------------------------------------------------- #

# Owned-channel event hook: keynotes, earnings prints, named launch
# events. Anchored on \b so "earningscall" doesn't false-positive.
EVENT_RE = re.compile(
    r"\b("
    r"keynote|earnings|financial results|investor day|shareholder|"
    r"GTC|WWDC|Google I/?O|Microsoft Build|Meta Connect|HPE Discover|"
    r"AI Day|Robotaxi|Cybercab|Investor Update|TERAFAB|"
    r"Q[1-4]\s*(?:20)?\d{2}|full year \d{4}|annual meeting|"
    r"reveal|unveil|launch event|delivery event"
    r")\b",
    re.IGNORECASE,
)

# Show-channel disambiguator: a speaker name within this many chars of
# a guest cue means "this is a real guest appearance," not a chapter-
# marker name-drop further down the description.
GUEST_CUE_RE = re.compile(
    r"\b("
    r"joins|sits? down with|in conversation with|interview with|"
    r"speaks? with|on the show|on the podcast|guest|episode with|"
    r"featuring|fireside chat|chats? with|talks? to|talks? with|"
    r"conversation with"
    r")\b",
    re.IGNORECASE,
)
GUEST_CUE_WINDOW = 80

_UA = "Mozilla/5.0 (compatible; ScannerBot/0.1; +intelligent-stock-scanner)"
_RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


# --------------------------------------------------------------------- #
# Dataclasses                                                           #
# --------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SpeakerSpec:
    speaker_id: str
    variants: tuple[str, ...]
    tickers: tuple[str, ...]
    amplifies: tuple[str, ...]
    authoritative_on: tuple[str, ...]
    tier: int
    ambiguous: bool = False
    requires_context: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    name: str
    channel_id: str
    handle: str
    owned: bool
    type: str
    notes: str = ""
    podcast_rss: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    channels: tuple[ChannelSpec, ...]
    speakers: tuple[SpeakerSpec, ...]


@dataclass(frozen=True, slots=True)
class VideoCandidate:
    """One discovery hit that survived the match rules.

    The name is historical — the same shape now covers podcast episodes
    too (Phase 3). ``source`` discriminates: ``"youtube"`` candidates
    have a real ``video_id`` and no audio URL; ``"podcast"`` candidates
    carry the direct enclosure URL in ``audio_url`` and a synthetic
    ``video_id`` of the form ``pc_<11char-hash>`` derived from the
    episode GUID so the primary key never collides with YouTube's 11-
    char IDs.

    ``url`` is always the canonical web URL (``watch?v=`` for YouTube,
    the episode page for podcasts). The timestamped ``youtu.be/...?t=``
    form is reserved for Phase 5 chunk deep-links.
    """

    channel: str
    channel_id: str
    video_id: str
    url: str
    title: str
    published_utc: datetime
    reason: str
    matched_speaker: str | None
    matched_tickers: tuple[str, ...]
    matched_text: str
    match_source: str
    co_speakers: tuple[str, ...] = ()
    source: str = "youtube"
    audio_url: str | None = None
    episode_guid: str | None = None


# --------------------------------------------------------------------- #
# Config loading                                                        #
# --------------------------------------------------------------------- #


def load_config(
    channels_path: Path = Path("config/channels.yaml"),
    speakers_path: Path = Path("config/speakers.yaml"),
) -> DiscoveryConfig:
    """Parse both YAML files into typed specs."""
    channels_data = yaml.safe_load(channels_path.read_text()) or {}
    speakers_data = yaml.safe_load(speakers_path.read_text()) or {}

    channels = tuple(
        ChannelSpec(
            name=str(c.get("name") or ""),
            channel_id=str(c.get("channel_id") or ""),
            handle=str(c.get("handle") or ""),
            owned=bool(c.get("owned", False)),
            type=str(c.get("type") or ""),
            notes=str(c.get("notes") or ""),
            podcast_rss=(str(c["podcast_rss"]) if c.get("podcast_rss") else None),
        )
        for c in (channels_data.get("channels") or [])
    )

    speakers = tuple(
        SpeakerSpec(
            speaker_id=sid,
            variants=tuple(sp.get("variants") or ()),
            tickers=tuple(sp.get("tickers") or ()),
            amplifies=tuple(sp.get("amplifies") or ()),
            authoritative_on=tuple(sp.get("authoritative_on") or ()),
            tier=int(sp.get("tier", 0)),
            ambiguous=bool(sp.get("ambiguous", False)),
            requires_context=tuple(sp.get("requires_context") or ()),
        )
        for sid, sp in (speakers_data.get("speakers") or {}).items()
    )

    return DiscoveryConfig(channels=channels, speakers=speakers)


# --------------------------------------------------------------------- #
# Public discovery API                                                  #
# --------------------------------------------------------------------- #


def discover_candidates(
    config: DiscoveryConfig,
    *,
    feed_loader: Callable[[str], feedparser.FeedParserDict] | None = None,
    max_workers: int = 8,
) -> list[VideoCandidate]:
    """Fetch every configured channel's RSS and return matched candidates.

    ``feed_loader`` is the test seam: pass a callable that returns a parsed
    ``FeedParserDict`` to keep the test suite hermetic. Production calls
    leave it as ``None`` and pay the HTTP round-trip per channel.
    """
    loader = feed_loader or _fetch_feed
    speaker_regex = _build_speaker_regex_map(config.speakers)
    speakers_by_id = {sp.speaker_id: sp for sp in config.speakers}

    actionable: list[ChannelSpec] = []
    for ch in config.channels:
        if not ch.channel_id:
            log.warning("skipping channel without channel_id: %s", ch.name)
            continue
        actionable.append(ch)

    out: list[VideoCandidate] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_worker, ch, loader, speaker_regex, speakers_by_id): ch
            for ch in actionable
        }
        for fut in as_completed(futures):
            ch = futures[fut]
            try:
                out.extend(fut.result())
            except Exception as exc:  # pragma: no cover - worker already swallows
                log.warning(
                    "discovery worker raised for %s (%s): %s",
                    ch.name, ch.channel_id, exc,
                )

    out.sort(key=lambda c: (-c.published_utc.timestamp(), c.channel, c.video_id))
    return out


def match_entries(
    channel: ChannelSpec,
    feed: feedparser.FeedParserDict,
    speaker_regex: dict[str, re.Pattern[str]],
    speakers: dict[str, SpeakerSpec],
) -> list[VideoCandidate]:
    """Apply the v2 match rules to one parsed feed. Pure function."""
    out: list[VideoCandidate] = []
    for entry in feed.entries:
        if _is_short(entry):
            continue
        candidate = _match_entry(channel, entry, speaker_regex, speakers)
        if candidate is not None:
            out.append(candidate)
    return out


# --------------------------------------------------------------------- #
# Per-entry matcher                                                     #
# --------------------------------------------------------------------- #


def _match_entry(
    channel: ChannelSpec,
    entry: Any,
    speaker_regex: dict[str, re.Pattern[str]],
    speakers: dict[str, SpeakerSpec],
) -> VideoCandidate | None:
    title = entry.get("title", "") or ""
    description = _description(entry)

    all_matches = _all_speaker_matches(title, description, speaker_regex, speakers)
    if all_matches:
        sid, source, matched_text = all_matches[0]
        sp = speakers[sid]
        co = tuple(m[0] for m in all_matches[1:])
        prefix = "owned+speaker" if channel.owned else "speaker"
        return _build_candidate(
            channel, entry,
            reason=f"{prefix}:{sid}",
            matched_speaker=sid,
            matched_tickers=sp.tickers + sp.amplifies,
            matched_text=matched_text,
            match_source=source,
            co_speakers=co,
        )

    # Show channels: speaker match is the only way in.
    if not channel.owned:
        return None

    event_match = _try_event_match(title, description)
    if event_match is not None:
        return _build_candidate(
            channel, entry,
            reason="owned+event",
            matched_speaker=None,
            matched_tickers=(),
            matched_text=event_match,
            match_source="owned",
        )

    return None


def _try_speaker_match(
    title: str,
    description: str,
    speaker_regex: dict[str, re.Pattern[str]],
    speakers: dict[str, SpeakerSpec],
) -> tuple[str, str, str] | None:
    """Return the single best (speaker_id, match_source, matched_text) or None.

    Thin wrapper over :func:`_all_speaker_matches`; preserved for callers
    that only need the primary match.
    """
    matches = _all_speaker_matches(title, description, speaker_regex, speakers)
    return matches[0] if matches else None


def _all_speaker_matches(
    title: str,
    description: str,
    speaker_regex: dict[str, re.Pattern[str]],
    speakers: dict[str, SpeakerSpec],
) -> list[tuple[str, str, str]]:
    """Return every speaker that matches, ordered by preference.

    First element is the primary; the rest become ``co_speakers``.

    Ordering:
      1. Title hits first (Rule C1), leftmost position wins.
      2. Then description hits (Rule C2), prefer speakers AFTER the cue
         (the "[HOST] sits down with [GUEST]" pattern), then leftmost,
         then alphabetical speaker_id.

    Ambiguous speakers without a corroborating ``requires_context`` term
    in title+description are dropped silently.
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    # Rule C1: title hits
    title_hits: list[tuple[int, str]] = []
    for sid in sorted(speaker_regex):
        sp = speakers[sid]
        m = speaker_regex[sid].search(title)
        if m is None:
            continue
        if sp.ambiguous and not _has_context(sp.requires_context, title, description):
            continue
        title_hits.append((m.start(), sid))
    title_hits.sort()
    title_snippet = title[:120].replace("\n", " ").strip()
    for _, sid in title_hits:
        if sid in seen:
            continue
        out.append((sid, "title", f"[TITLE] {title_snippet}"))
        seen.add(sid)

    if not description:
        return out

    # Rule C2: description hits with cue, prefer guests (after cue) over hosts.
    desc_hits: list[tuple[int, int, str, int, int]] = []
    for sid in sorted(speaker_regex):
        if sid in seen:
            continue
        sp = speakers[sid]
        for m in speaker_regex[sid].finditer(description):
            cue = _find_cue_near(description, m.start(), m.end(), GUEST_CUE_WINDOW)
            if cue is None:
                continue
            if sp.ambiguous and not _has_context(sp.requires_context, title, description):
                continue
            host_flag = 0 if m.start() >= cue[1] else 1
            desc_hits.append((host_flag, m.start(), sid, m.start(), m.end()))
            break  # one match per speaker is enough
    desc_hits.sort()
    for _, _, sid, s_start, s_end in desc_hits:
        if sid in seen:
            continue
        lo = max(0, s_start - GUEST_CUE_WINDOW)
        hi = min(len(description), s_end + GUEST_CUE_WINDOW)
        snip = description[lo:hi].replace("\n", " ").strip()
        out.append((sid, "description", f"[DESC] {snip}"))
        seen.add(sid)

    return out


def _try_event_match(title: str, description: str) -> str | None:
    """Rule B2: event-keyword scan over title + first 300 chars of description."""
    m = EVENT_RE.search(title)
    if m is not None:
        return f"[TITLE] {m.group(0)}"
    m = EVENT_RE.search(description[:300])
    if m is not None:
        return f"[DESC] {m.group(0)}"
    return None


def _find_cue_near(
    text: str, speaker_start: int, speaker_end: int, window: int
) -> tuple[int, int] | None:
    """Return ``(cue_start, cue_end)`` for the first GUEST_CUE within
    ±window chars of the speaker match, or None."""
    lo = max(0, speaker_start - window)
    hi = min(len(text), speaker_end + window)
    m = GUEST_CUE_RE.search(text, lo, hi)
    if m is None:
        return None
    return (m.start(), m.end())


def _has_context(terms: tuple[str, ...], title: str, description: str) -> bool:
    if not terms:
        return False
    haystack = f"{title}\n{description}".lower()
    return any(t.lower() in haystack for t in terms)


# --------------------------------------------------------------------- #
# Speaker regex compilation                                             #
# --------------------------------------------------------------------- #


def _build_speaker_regex_map(
    speakers: tuple[SpeakerSpec, ...],
) -> dict[str, re.Pattern[str]]:
    """One word-boundary regex per speaker; longer variants come first
    so "Lisa Su" wins over a hypothetical bare "Lisa"."""
    out: dict[str, re.Pattern[str]] = {}
    for sp in speakers:
        if not sp.variants:
            continue
        escaped = sorted({re.escape(v) for v in sp.variants if v}, key=len, reverse=True)
        if not escaped:
            continue
        pattern = r"\b(?:" + "|".join(escaped) + r")\b"
        out[sp.speaker_id] = re.compile(pattern, re.IGNORECASE)
    return out


# --------------------------------------------------------------------- #
# Entry projection                                                      #
# --------------------------------------------------------------------- #


def _is_short(entry: Any) -> bool:
    link = entry.get("link", "") or ""
    return "/shorts/" in link


def _description(entry: Any) -> str:
    """Concatenate every distinct description-ish field feedparser exposes.

    feedparser collapses ``<media:description>`` into ``summary`` on the
    YouTube schema today, but older versions and other Atom dialects
    expose ``media_description`` separately. Reading both is cheap
    insurance against version drift.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for key in ("summary", "media_description"):
        val = entry.get(key) or ""
        if val and val not in seen:
            parts.append(val)
            seen.add(val)
    return "\n".join(parts)


def _published_utc(entry: Any) -> datetime:
    """Promote feedparser's naive ``time.struct_time`` to tz-aware UTC."""
    tup = entry.get("published_parsed") or entry.get("updated_parsed")
    if tup is None:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    return datetime(
        tup[0], tup[1], tup[2], tup[3], tup[4], tup[5],
        tzinfo=timezone.utc,
    )


def _video_id(entry: Any) -> str:
    vid = entry.get("yt_videoid") or ""
    if vid:
        return vid
    link = entry.get("link") or ""
    m = re.search(r"[?&]v=([\w-]{11})", link)
    if m is not None:
        return m.group(1)
    m = re.search(r"/shorts/([\w-]{11})", link)
    if m is not None:
        return m.group(1)
    return ""


def _build_candidate(
    channel: ChannelSpec,
    entry: Any,
    *,
    reason: str,
    matched_speaker: str | None,
    matched_tickers: tuple[str, ...],
    matched_text: str,
    match_source: str,
    co_speakers: tuple[str, ...] = (),
) -> VideoCandidate:
    video_id = _video_id(entry)
    return VideoCandidate(
        channel=channel.name,
        channel_id=channel.channel_id,
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=(entry.get("title") or "").strip(),
        published_utc=_published_utc(entry),
        reason=reason,
        matched_speaker=matched_speaker,
        matched_tickers=matched_tickers,
        matched_text=matched_text,
        match_source=match_source,
        co_speakers=co_speakers,
        source="youtube",
        audio_url=None,
        episode_guid=None,
    )


# --------------------------------------------------------------------- #
# RSS fetching                                                          #
# --------------------------------------------------------------------- #


def _worker(
    channel: ChannelSpec,
    loader: Callable[[str], feedparser.FeedParserDict],
    speaker_regex: dict[str, re.Pattern[str]],
    speakers: dict[str, SpeakerSpec],
) -> list[VideoCandidate]:
    try:
        feed = loader(channel.channel_id)
    except Exception as exc:
        log.warning(
            "feed fetch failed for %s (%s): %s",
            channel.name, channel.channel_id, exc,
        )
        return []
    return match_entries(channel, feed, speaker_regex, speakers)


def _fetch_feed(channel_id: str, *, timeout: int = 20) -> feedparser.FeedParserDict:
    url = _RSS_BASE.format(channel_id=channel_id)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return feedparser.parse(r.read())
