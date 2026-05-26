"""Modal entrypoint for transcript ingestion.

Scheduled nightly via :func:`scheduled_ingest`. Manual/ad-hoc runs go
through :func:`manual_ingest`. Both delegate to
:func:`src.narrative.sources.youtube.driver.run_ingest` so the heavy
orchestration logic is Modal-free and unit-testable.

Container image
---------------
The project's full ``requirements.txt`` is installed so the parent
package ``src.narrative.sources.__init__`` (which imports
``polygon_news``, ``yfinance_news`` siblings) doesn't ImportError when
Python walks the package on first import. On top of that we layer the
three ingestion-only deps and ``ffmpeg`` for ``yt-dlp`` audio decode.
The Whisper ``small.en`` weights are pre-downloaded at image build so
runtime cold starts skip the ~500 MB pull.

Volume
------
We re-use the project's existing ``stock_data`` Modal Volume mounted at
``/data`` — same convention as :mod:`src.modal_app.app` — and write the
SQLite file at ``/data/narrative/transcripts.db``. The driver calls
``volume.commit()`` every 5 episodes via the ``on_progress`` callback so
partial progress survives container timeouts.

Secret
------
``modal.Secret.from_name("transcript-ingestion")`` is expected to exist
on the user's Modal account (even if empty). Required keys when
populated::

    WEBSHARE_PROXY_USERNAME
    WEBSHARE_PROXY_PASSWORD

When the secret is empty (no Webshare yet), YouTube paths skip cleanly
with ``no_proxy_configured`` and only podcast Path 0 runs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import modal

# ---------------------------------------------------------------------- #
# Volume + image + app                                                   #
# ---------------------------------------------------------------------- #

# NOTE on paths:
# Modal places this entrypoint at /root/modal_app.py at runtime (flattened
# to the basename, not its original src/narrative/sources/youtube/ path).
# That means:
#  * No `Path(__file__).resolve().parents[N]` math at module top — the
#    container only has 2 parents, blowing up any N>=2.
#  * Image-chain paths must be cwd-relative (resolved at `modal deploy`
#    time, where cwd = repo root).
#  * Function-body imports must be ABSOLUTE (`from src...`), not relative
#    (`from .driver import ...`) — /root/modal_app.py isn't part of the
#    package, so relative imports fail.

VOLUME_NAME = "stock_data"
VOLUME_MOUNT = "/data"
DB_PATH_IN_CONTAINER = f"{VOLUME_MOUNT}/narrative/transcripts.db"
CHANNELS_PATH_IN_CONTAINER = Path("/root/config/channels.yaml")
SPEAKERS_PATH_IN_CONTAINER = Path("/root/config/speakers.yaml")

stock_data_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

# Image layering: project requirements first (because the youtube
# subpackage's parent __init__ imports siblings that need requests,
# yfinance, etc.), then the ingestion-only deps, then the model cache.
ingest_image = (
    # nvidia/cuda runtime image so libcublas.so.12 / libcudnn.so are
    # already on the system — debian_slim doesn't ship CUDA libs, and
    # faster-whisper/ctranslate2 will RuntimeError without them.
    modal.Image.from_registry(
        "nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04",
        add_python="3.12",
    )
    .apt_install("ffmpeg")
    .pip_install_from_requirements("requirements.txt")
    .pip_install(
        "youtube-transcript-api>=1.0.0",
        "yt-dlp>=2024.7.16",
        # ctranslate2 4.5+ and faster-whisper 1.0.4+ are linked
        # against cuDNN 9, which the nvidia/cuda:12.4.1-cudnn-runtime
        # base image ships. Older pins (ctranslate2 4.4.0,
        # faster-whisper 1.0.3) want cuDNN 8 and SIGABRT on this image.
        "ctranslate2>=4.5.0,<5.0",
        "faster-whisper>=1.0.4,<2.0",
    )
    .run_commands(
        "python -c 'from faster_whisper import WhisperModel; "
        'WhisperModel("small.en", device="cpu", compute_type="int8")\'',
    )
    .add_local_python_source("src")
    .add_local_dir("config", "/root/config")
)

# Secret may be empty until the user signs up for Webshare. The fetcher
# checks env-var truthiness, so an empty secret cleanly disables the
# YouTube paths without breaking deploy.
ingestion_secret = modal.Secret.from_name("transcript-ingestion")

app = modal.App("transcript-ingestion")


def _on_progress(_n: int) -> None:
    """Flush partial progress to the Volume.

    Wrapped in try/except because a transient commit failure shouldn't
    abort the run — the worst case is the next commit covers more rows.
    """
    try:
        stock_data_volume.commit()
    except Exception as exc:  # pragma: no cover - infrastructure
        logging.getLogger(__name__).warning("volume.commit() failed: %s", exc)


# ---------------------------------------------------------------------- #
# Scheduled run                                                          #
# ---------------------------------------------------------------------- #


@app.function(
    image=ingest_image,
    gpu="T4",
    schedule=modal.Cron("17 4 * * *"),  # 04:17 UTC daily
    timeout=60 * 60 * 4,                # 4 hours
    volumes={VOLUME_MOUNT: stock_data_volume},
    secrets=[ingestion_secret],
)
def scheduled_ingest() -> dict[str, Any]:
    """Nightly scheduled ingestion. Returns the run summary."""
    from src.narrative.sources.youtube.driver import run_ingest
    return run_ingest(
        db_path=DB_PATH_IN_CONTAINER,
        channels_path=CHANNELS_PATH_IN_CONTAINER,
        speakers_path=SPEAKERS_PATH_IN_CONTAINER,
        on_progress=_on_progress,
    )


# ---------------------------------------------------------------------- #
# Manual / ad-hoc trigger                                                #
# ---------------------------------------------------------------------- #


@app.function(
    image=ingest_image,
    gpu="T4",
    timeout=60 * 60 * 4,
    volumes={VOLUME_MOUNT: stock_data_volume},
    secrets=[ingestion_secret],
)
def manual_ingest(
    *,
    limit: int | None = None,
    episode_ids: list[str] | None = None,
    podcast_only: bool = False,
    youtube_only: bool = False,
    dry_run: bool = False,
    retry_failed: bool = False,
    lookback_days: int = 30,
    whisper_model_name: str = "small.en",
) -> dict[str, Any]:
    """Manual trigger with arg overrides. Used by ``scripts/run_ingest_locally.py``."""
    from src.narrative.sources.youtube.driver import run_ingest
    return run_ingest(
        db_path=DB_PATH_IN_CONTAINER,
        channels_path=CHANNELS_PATH_IN_CONTAINER,
        speakers_path=SPEAKERS_PATH_IN_CONTAINER,
        limit=limit,
        episode_ids=episode_ids,
        podcast_only=podcast_only,
        youtube_only=youtube_only,
        dry_run=dry_run,
        retry_failed=retry_failed,
        lookback_days=lookback_days,
        whisper_model_name=whisper_model_name,
        on_progress=None if dry_run else _on_progress,
    )


# ---------------------------------------------------------------------- #
# Ad-hoc DB queries                                                      #
# ---------------------------------------------------------------------- #


@app.function(
    image=ingest_image,
    timeout=120,
    volumes={VOLUME_MOUNT: stock_data_volume},
)
def query_chunks(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """FTS5 search over chunk text. Returns up to ``limit`` hits."""
    from src.narrative.sources.youtube.transcript_store import TranscriptStore
    with TranscriptStore(DB_PATH_IN_CONTAINER) as store:
        return [
            {
                "episode_id": r[0],
                "chunk_idx": r[1],
                "start_s": r[2],
                "end_s": r[3],
                "text": r[4][:240],
            }
            for r in store.search_chunks(query, limit=limit)
        ]


@app.function(
    image=ingest_image,
    timeout=60,
    volumes={VOLUME_MOUNT: stock_data_volume},
)
def db_stats() -> dict[str, Any]:
    """Quick counts for sanity checking.

    Splits ``episodes_live`` vs ``episodes_backfill`` so Phase 3.5
    backfill progress is visible alongside live nightly counts.
    """
    # Touching the store also triggers the is_backfill column migration
    # the first time this runs against a pre-Phase-3.5 DB on the volume.
    from src.narrative.sources.youtube.transcript_store import TranscriptStore
    with TranscriptStore(DB_PATH_IN_CONTAINER) as store:
        conn = store._conn
        return {
            "episodes": conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
            "episodes_live": conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE is_backfill = 0"
            ).fetchone()[0],
            "episodes_backfill": conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE is_backfill = 1"
            ).fetchone()[0],
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "by_source": dict(conn.execute(
                "SELECT source, COUNT(*) FROM episodes GROUP BY source"
            ).fetchall()),
            "by_method": dict(conn.execute(
                "SELECT source_method, COUNT(*) FROM episodes GROUP BY source_method"
            ).fetchall()),
            "recent_failures": conn.execute(
                "SELECT COUNT(*) FROM ingest_log WHERE status='failed' "
                "AND attempted_at > datetime('now', '-7 days')"
            ).fetchone()[0],
        }


# ---------------------------------------------------------------------- #
# One-shot historical backfill (Phase 3.5)                              #
# ---------------------------------------------------------------------- #
#
# TODO Phase 3.5.1: a backfill_episodes_parallel function that uses
# Function.map() to spread N episodes across K T4 containers. Cuts a
# 500-episode backfill from ~25h to ~5h with K=5. Cost is roughly the
# same (you pay for GPU-seconds, not wall time). Skipped in initial
# implementation because the single-container path is correct and
# idempotent — partial progress survives via volume commits every 5
# episodes, so a timed-out run resumes cleanly via has_episode() dedup.


@app.function(
    image=ingest_image,
    gpu="A10G",                         # ~2.5x faster than T4, ~25% more cost
    timeout=60 * 60 * 24,               # 24 hours — bounded by max_episodes
    volumes={VOLUME_MOUNT: stock_data_volume},
    secrets=[ingestion_secret],
)
def backfill_episodes(
    *,
    lookback_days: int = 540,
    max_episodes: int = 500,
    speaker_tiers: list[int] | None = None,
    dry_run: bool = False,
    whisper_model_name: str = "small.en",
) -> dict[str, Any]:
    """One-shot historical backfill — manually invoked, NOT scheduled.

    ``speaker_tiers`` is a list (rather than frozenset) so the function
    signature is JSON-friendly when invoked via ``modal.Function.remote()``.
    ``None`` becomes ``frozenset({1})`` (tier-1 only).
    """
    from src.narrative.sources.youtube.driver import run_backfill
    tiers: frozenset[int] = (
        frozenset(speaker_tiers) if speaker_tiers is not None else frozenset({1})
    )
    return run_backfill(
        db_path=DB_PATH_IN_CONTAINER,
        channels_path=CHANNELS_PATH_IN_CONTAINER,
        speakers_path=SPEAKERS_PATH_IN_CONTAINER,
        lookback_days=lookback_days,
        max_episodes=max_episodes,
        speaker_tiers=tiers,
        dry_run=dry_run,
        whisper_model_name=whisper_model_name,
        on_progress=None if dry_run else _on_progress,
    )


# ---------------------------------------------------------------------- #
# Phase 3.7 — Daily Narrative Briefing                                   #
# ---------------------------------------------------------------------- #
#
# Requires ``ANTHROPIC_API_KEY`` to live in the ``transcript-ingestion``
# Modal secret alongside the Webshare credentials. The smoke + manual
# triggers will surface a clear auth error if it's missing.
#
# The daily cron schedule below is COMMENTED OUT pending review of the
# v1 smoke output (per Phase 3.7 spec §15 stop-conditions). Uncomment
# and re-run ``modal deploy`` to enable nightly briefings at 05:30 UTC.


BRIEFINGS_DIR_IN_CONTAINER = Path("/data/briefings")


@app.function(
    image=ingest_image,
    # 1 hour — safety net. Parallel picks researcher (Phase 3.7.3) should
    # finish in ~2-5 min, but margin lets the briefing tolerate a growing
    # corpus + slow web_search round trips without container-killing.
    timeout=60 * 60,
    volumes={VOLUME_MOUNT: stock_data_volume},
    secrets=[ingestion_secret],
    schedule=modal.Cron("30 5 * * *"),   # 05:30 UTC daily — enabled 2026-05-26
)
def daily_briefing() -> dict[str, Any]:
    """Daily briefing generation. Runs an hour after scheduled_ingest."""
    from datetime import datetime, timezone

    from src.narrative.briefing.briefing import run_briefing

    result = run_briefing(
        db_path=DB_PATH_IN_CONTAINER,
        briefing_date=datetime.now(timezone.utc).date(),
        lookback_hours=24,
        briefings_dir=BRIEFINGS_DIR_IN_CONTAINER,
        dry_run=False,
    )
    try:
        stock_data_volume.commit()
    except Exception as exc:  # pragma: no cover - infrastructure
        logging.getLogger(__name__).warning("post-briefing commit failed: %s", exc)
    return result


@app.function(
    image=ingest_image,
    timeout=60 * 60,                  # 1 hour — matches daily_briefing safety net
    volumes={VOLUME_MOUNT: stock_data_volume},
    secrets=[ingestion_secret],
)
def manual_briefing(
    briefing_date: str | None = None,
    lookback_hours: int = 24,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Manual trigger. ``briefing_date`` is ISO ``YYYY-MM-DD`` (defaults to today)."""
    from datetime import date as date_, datetime, timezone

    from src.narrative.briefing.briefing import run_briefing

    target = date_.fromisoformat(briefing_date) if briefing_date else datetime.now(timezone.utc).date()
    result = run_briefing(
        db_path=DB_PATH_IN_CONTAINER,
        briefing_date=target,
        lookback_hours=int(lookback_hours),
        briefings_dir=BRIEFINGS_DIR_IN_CONTAINER,
        dry_run=bool(dry_run),
    )
    if not dry_run:
        try:
            stock_data_volume.commit()
        except Exception as exc:  # pragma: no cover - infrastructure
            logging.getLogger(__name__).warning("post-briefing commit failed: %s", exc)
    return result
