"""Orchestration for one ingestion run.

The :func:`run_ingest` function is the single entry point used by both
Modal functions (scheduled + manual) and any local invocation. Keeping
it Modal-free means it can be unit-tested without spinning up a
container.

Per-run flow:

1. Load ``channels.yaml`` + ``speakers.yaml``.
2. Run YouTube discovery and podcast discovery (both pure RSS — no proxy).
3. Dedupe across sources: a podcast episode within ±2 days of a YouTube
   upload with ≥0.8 title similarity wins; the YouTube copy is dropped.
4. Filter against the store: skip episodes already ingested or in
   failure cooldown.
5. Sort by ``(speaker_tier, -published_utc)`` so T1 names come first.
6. For each candidate: fetch → chunk → write → log_attempt.
7. Call ``on_progress(n)`` every 5 episodes so the Modal caller can
   ``volume.commit()`` partial progress without polluting this module
   with Modal imports.
8. Return a summary dict.

The driver does NOT call ``modal.Volume.commit()`` directly. The
Modal function owns the volume; it passes a small callback.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from .chunker import rechunk
from .discovery import SpeakerSpec, VideoCandidate, discover_candidates, load_config
from .podcast_discovery import discover_podcast_candidates
from .transcript_fetchers import (
    TranscriptUnavailable,
    fetch_transcript,
    has_proxy,
)
from .transcript_store import DEFAULT_DB_PATH, TranscriptStore

log = logging.getLogger(__name__)

RETRY_FAILED_AFTER_DAYS = 7

# Phase 3.5 cost-estimate: roughly USD/episode on a T4 with small.en
# Whisper, averaging across a typical mix of 60-90 min podcast episodes.
# A 30% safety margin gives the upper bound shown in dry-run output.
_BACKFILL_COST_PER_EPISODE_USD = 0.075
_BACKFILL_COST_SAFETY_MARGIN = 1.30


def run_ingest(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    channels_path: Path = Path("config/channels.yaml"),
    speakers_path: Path = Path("config/speakers.yaml"),
    limit: int | None = None,
    episode_ids: list[str] | None = None,
    podcast_only: bool = False,
    youtube_only: bool = False,
    dry_run: bool = False,
    retry_failed: bool = False,
    lookback_days: int = 30,
    whisper_model_name: str = "small.en",
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """Run one ingestion pass. Returns a summary dict."""
    if podcast_only and youtube_only:
        raise ValueError("podcast_only and youtube_only are mutually exclusive")
    if youtube_only and not has_proxy():
        raise RuntimeError(
            "youtube_only requires WEBSHARE_PROXY_USERNAME/WEBSHARE_PROXY_PASSWORD"
        )

    t0 = time.monotonic()
    config = load_config(channels_path=channels_path, speakers_path=speakers_path)
    speakers = {sp.speaker_id: sp for sp in config.speakers}

    # ---- Discovery -------------------------------------------------- #
    youtube_cands: list[VideoCandidate] = []
    podcast_cands: list[VideoCandidate] = []
    if not podcast_only:
        log.info("running YouTube discovery (%d channels with channel_id)",
                 sum(1 for c in config.channels if c.channel_id))
        youtube_cands = discover_candidates(config)
    if not youtube_only:
        actionable = sum(1 for c in config.channels if c.podcast_rss)
        log.info("running podcast discovery (%d channels with podcast_rss)", actionable)
        podcast_cands = discover_podcast_candidates(config, lookback_days=lookback_days)

    discovered_yt = len(youtube_cands)
    discovered_pc = len(podcast_cands)
    merged = _dedup_candidates(podcast_cands + youtube_cands)

    if episode_ids:
        wanted = {x.strip() for x in episode_ids if x.strip()}
        merged = [c for c in merged if c.video_id in wanted]

    # ---- Filter against store --------------------------------------- #
    with TranscriptStore(db_path) as store:
        to_process = _filter_pending(merged, store, retry_failed=retry_failed)

    by_source_pre = Counter(c.source for c in to_process)
    to_process.sort(key=lambda c: _sort_key(c, speakers))
    if limit:
        to_process = to_process[:limit]

    if dry_run:
        return {
            "dry_run": True,
            "discovered": {"podcast": discovered_pc, "youtube": discovered_yt},
            "after_filter": len(to_process),
            "after_filter_by_source": dict(by_source_pre),
            "would_process": [
                {
                    "source": c.source,
                    "video_id": c.video_id,
                    "channel": c.channel,
                    "title": c.title,
                    "matched_speaker": c.matched_speaker,
                }
                for c in to_process
            ],
        }

    # ---- Whisper model (load once) ---------------------------------- #
    whisper = _load_whisper(whisper_model_name)

    # ---- Fetch loop ------------------------------------------------- #
    method_counts: Counter[str] = Counter()
    failed = 0
    processed = 0
    skipped_no_whisper = 0
    skipped_no_proxy = 0

    with TranscriptStore(db_path) as store, tempfile.TemporaryDirectory(prefix="ingest_") as wd:
        workdir = Path(wd)
        for i, cand in enumerate(to_process):
            if cand.source == "podcast" and whisper is None:
                store.log_attempt(cand.video_id, status="skipped",
                                  error="no whisper model")
                skipped_no_whisper += 1
                continue
            log.info("fetching %s/%s (%s)", cand.channel, cand.title[:60], cand.source)
            try:
                result = fetch_transcript(
                    cand, workdir=workdir, whisper_model=whisper,
                )
            except TranscriptUnavailable as e:
                reason = str(e)
                log.warning("fetch failed for %s: %s", cand.video_id, reason)
                store.log_attempt(cand.video_id, status="failed",
                                  error=reason[:500])
                if "no_proxy_configured" in reason:
                    skipped_no_proxy += 1
                else:
                    failed += 1
                continue
            chunks = rechunk(result.segments)
            store.write_episode(
                cand,
                chunks=chunks,
                duration_s=result.duration_s,
                source_method=result.source_method,
            )
            store.log_attempt(cand.video_id, status="ok",
                              source_method=result.source_method)
            method_counts[result.source_method] += 1
            processed += 1

            if on_progress is not None and (i + 1) % 5 == 0:
                on_progress(i + 1)

        if on_progress is not None:
            on_progress(len(to_process))

    elapsed_s = time.monotonic() - t0
    return {
        "dry_run": False,
        "discovered": {"podcast": discovered_pc, "youtube": discovered_yt},
        "after_filter": len(to_process),
        "after_filter_by_source": dict(by_source_pre),
        "processed": processed,
        "by_method": dict(method_counts),
        "failed": failed,
        "skipped_no_proxy": skipped_no_proxy,
        "skipped_no_whisper": skipped_no_whisper,
        "elapsed_s": round(elapsed_s, 1),
    }


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #


def _dedup_candidates(candidates: list[VideoCandidate]) -> list[VideoCandidate]:
    """Drop YouTube candidates that look like a podcast equivalent.

    Match on identical channel name AND ±2-day publication window AND
    ``>=0.8`` ``difflib.SequenceMatcher.ratio()`` title similarity. We
    prefer the podcast version because Path 0 is cheaper and proxy-free.
    """
    podcasts = [c for c in candidates if c.source == "podcast"]
    youtubes = [c for c in candidates if c.source == "youtube"]
    podcasts_by_channel: dict[str, list[VideoCandidate]] = {}
    for pc in podcasts:
        podcasts_by_channel.setdefault(pc.channel, []).append(pc)

    def has_pod_dup(yt: VideoCandidate) -> bool:
        for pc in podcasts_by_channel.get(yt.channel, ()):
            if abs((pc.published_utc - yt.published_utc).days) > 2:
                continue
            sim = SequenceMatcher(None, pc.title.lower(), yt.title.lower()).ratio()
            if sim >= 0.8:
                return True
        return False

    kept_yt = [yt for yt in youtubes if not has_pod_dup(yt)]
    return podcasts + kept_yt


def _filter_pending(
    candidates: list[VideoCandidate],
    store: TranscriptStore,
    *,
    retry_failed: bool,
) -> list[VideoCandidate]:
    """Skip candidates already in the DB or in failure cooldown."""
    out: list[VideoCandidate] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETRY_FAILED_AFTER_DAYS)
    for c in candidates:
        if store.has_episode(c.video_id):
            continue
        last = store.last_attempt(c.video_id)
        if last is None:
            out.append(c)
            continue
        ts, status = last
        if status == "ok":
            continue  # has_episode should have caught this; defensive
        if status == "failed":
            if retry_failed and ts < cutoff:
                out.append(c)
            continue
        out.append(c)  # "skipped" or other → let it through
    return out


def _sort_key(c: VideoCandidate, speakers: dict[str, SpeakerSpec]) -> tuple[int, float]:
    """Tier-then-recency. Owned events (no speaker) sink to tier 99."""
    tier = 99
    if c.matched_speaker and c.matched_speaker in speakers:
        tier = speakers[c.matched_speaker].tier
    return (tier, -c.published_utc.timestamp())


# --------------------------------------------------------------------- #
# Phase 3.5 — one-shot historical backfill                              #
# --------------------------------------------------------------------- #


def run_backfill(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    channels_path: Path = Path("config/channels.yaml"),
    speakers_path: Path = Path("config/speakers.yaml"),
    lookback_days: int = 540,
    max_episodes: int = 500,
    speaker_tiers: frozenset[int] = frozenset({1}),
    dry_run: bool = False,
    whisper_model_name: str = "small.en",
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """One-shot historical backfill of podcast episodes.

    Pulls full RSS feeds (no 30-day cap), filters to candidates whose
    primary speaker is in ``speaker_tiers``, and runs them through the
    same fetch → chunk → store pipeline as live ingest but with
    ``is_backfill=True`` on the resulting episode rows.

    ``dry_run=True`` skips the Whisper load and the fetch loop entirely;
    returns a candidates_count + cost estimate dict so the operator can
    sanity-check spend before committing GPU minutes.

    Idempotent across re-runs: ``store.has_episode()`` filters anything
    already ingested, so a partial run can be resumed by re-invoking
    with the same args.
    """
    if max_episodes <= 0:
        raise ValueError(f"max_episodes must be > 0, got {max_episodes}")

    t0 = time.monotonic()
    config = load_config(channels_path=channels_path, speakers_path=speakers_path)
    speakers = {sp.speaker_id: sp for sp in config.speakers}

    log.info(
        "backfill discovery: %d podcast feeds, %d-day lookback, tiers=%s",
        sum(1 for c in config.channels if c.podcast_rss),
        lookback_days,
        sorted(speaker_tiers),
    )
    candidates = discover_podcast_candidates(
        config,
        lookback_days=lookback_days,
        filter_to_speaker_tiers=speaker_tiers,
    )
    discovered_count = len(candidates)

    with TranscriptStore(db_path) as store:
        candidates = [c for c in candidates if not store.has_episode(c.video_id)]
    after_filter_count = len(candidates)

    candidates.sort(key=lambda c: _sort_key(c, speakers))
    if len(candidates) > max_episodes:
        candidates = candidates[:max_episodes]

    base_cost = len(candidates) * _BACKFILL_COST_PER_EPISODE_USD
    cost_range = {
        "low": round(base_cost, 2),
        "high": round(base_cost * _BACKFILL_COST_SAFETY_MARGIN, 2),
    }

    if dry_run:
        log.info(
            "dry-run: %d candidates after filter, est. cost $%.2f-$%.2f",
            len(candidates), cost_range["low"], cost_range["high"],
        )
        return {
            "dry_run": True,
            "is_backfill": True,
            "discovered": {"podcast": discovered_count, "youtube": 0},
            "after_filter": after_filter_count,
            "candidates_count": len(candidates),
            "estimated_cost_usd": cost_range,
            "speaker_tiers": sorted(speaker_tiers),
            "lookback_days": lookback_days,
            "elapsed_s": round(time.monotonic() - t0, 1),
        }

    whisper = _load_whisper(whisper_model_name)
    if whisper is None:
        # Backfill is podcast-only and every podcast path needs Whisper;
        # without it there's no useful work to do.
        return {
            "dry_run": False,
            "is_backfill": True,
            "discovered": {"podcast": discovered_count, "youtube": 0},
            "after_filter": after_filter_count,
            "processed": 0,
            "by_method": {},
            "failed": 0,
            "skipped_no_whisper": len(candidates),
            "estimated_cost_usd": cost_range,
            "elapsed_s": round(time.monotonic() - t0, 1),
        }

    method_counts: Counter[str] = Counter()
    failed = 0
    processed = 0

    with TranscriptStore(db_path) as store, tempfile.TemporaryDirectory(prefix="backfill_") as wd:
        workdir = Path(wd)
        for i, cand in enumerate(candidates):
            log.info("backfill %d/%d %s/%s", i + 1, len(candidates),
                     cand.channel, cand.title[:60])
            try:
                result = fetch_transcript(
                    cand, workdir=workdir, whisper_model=whisper,
                )
            except TranscriptUnavailable as e:
                store.log_attempt(cand.video_id, status="failed",
                                  error=f"backfill: {e}"[:500])
                failed += 1
                continue
            chunks = rechunk(result.segments)
            store.write_episode(
                cand,
                chunks=chunks,
                duration_s=result.duration_s,
                source_method=result.source_method,
                is_backfill=True,
            )
            store.log_attempt(cand.video_id, status="ok",
                              source_method=result.source_method)
            method_counts[result.source_method] += 1
            processed += 1

            if on_progress is not None and (i + 1) % 5 == 0:
                on_progress(i + 1)

        if on_progress is not None and len(candidates) > 0:
            on_progress(len(candidates))

    return {
        "dry_run": False,
        "is_backfill": True,
        "discovered": {"podcast": discovered_count, "youtube": 0},
        "after_filter": after_filter_count,
        "candidates_count": len(candidates),
        "processed": processed,
        "by_method": dict(method_counts),
        "failed": failed,
        "speaker_tiers": sorted(speaker_tiers),
        "lookback_days": lookback_days,
        "elapsed_s": round(time.monotonic() - t0, 1),
    }


def _load_whisper(model_name: str) -> Any:
    """Load faster-whisper with GPU when available, CPU otherwise.

    Returns ``None`` (with a warning) if the library is missing. The
    fetcher will then skip podcast candidates and fall through to
    caption-only YouTube paths.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as e:
        log.warning("faster-whisper not installed (%s); whisper paths disabled", e)
        return None
    log.info("loading whisper model %s", model_name)
    # device="auto" + compute_type="float16" → GPU when CUDA visible,
    # falls back to CPU/int8 otherwise.
    try:
        return WhisperModel(model_name, device="auto", compute_type="float16")
    except (ValueError, RuntimeError) as e:
        log.warning("float16 compute failed (%s); retrying CPU/int8", e)
        return WhisperModel(model_name, device="cpu", compute_type="int8")
