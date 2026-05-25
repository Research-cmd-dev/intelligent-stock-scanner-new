"""Phase 3.7 orchestrator.

Reads recently-ingested episodes from ``transcripts.db``, calls Claude
Haiku per episode, calls Claude Sonnet once for the daily aggregation,
renders Markdown, and (unless ``dry_run``) writes both the DB row and
the volume Markdown sidecar.

Designed for two callers:
  * the Modal scheduled function (``daily_briefing`` at 05:30 UTC)
  * a local CLI (``scripts/generate_briefing.py``)
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..sources.youtube.transcript_store import TranscriptStore
from .llm_aggregator import DEFAULT_MODEL as SONNET_MODEL
from .llm_aggregator import aggregate_daily
from .llm_summarizer import DEFAULT_MODEL as HAIKU_MODEL
from .llm_summarizer import summarize_episode
from .markdown_formatter import to_markdown
from .storage import DEFAULT_BRIEFINGS_DIR, briefing_id_for, write_markdown


log = logging.getLogger(__name__)


def run_briefing(
    *,
    db_path: str | Path,
    briefing_date: date_ | None = None,
    lookback_hours: int = 24,
    client: Any = None,
    haiku_model: str = HAIKU_MODEL,
    sonnet_model: str = SONNET_MODEL,
    briefings_dir: Path = DEFAULT_BRIEFINGS_DIR,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate the briefing for ``briefing_date`` (default: today UTC).

    ``client`` is an ``anthropic.Anthropic`` instance (or a stub for
    tests). When ``None`` and not dry-run, one is constructed lazily.

    Returns the structured briefing dict. On no-new-episodes, returns
    ``{"status": "no_new_episodes", ...}`` without calling the LLM.
    """
    now = datetime.now(timezone.utc)
    target_date = briefing_date or now.date()
    cutoff = now - timedelta(hours=int(lookback_hours))

    with TranscriptStore(str(db_path)) as store:
        episodes = store.episodes_ingested_since(cutoff)
        if not episodes:
            return {
                "status": "no_new_episodes",
                "briefing_date": target_date.isoformat(),
                "lookback_hours": int(lookback_hours),
                "cutoff_utc": cutoff.isoformat(),
            }

        episode_chunks: dict[str, list[dict[str, Any]]] = {}
        for ep in episodes:
            ep_id = ep["episode_id"]
            episode_chunks[ep_id] = [
                {"chunk_idx": c.idx, "start_s": c.start_s, "end_s": c.end_s, "text": c.text}
                for c in store.chunks_for_episode(ep_id)
            ]

        recent = store.list_recent_briefings(days=14)

    active_client = client if client is not None else _build_anthropic_client()

    summaries: list[dict[str, Any]] = []
    for ep in episodes:
        try:
            ep_summary = summarize_episode(
                episode=ep,
                chunks=episode_chunks[ep["episode_id"]],
                client=active_client,
                model=haiku_model,
            )
        except Exception as exc:
            log.warning("summarizer raised for %s: %s", ep["episode_id"], exc)
            continue
        summaries.append(ep_summary)

    aggregation = aggregate_daily(
        briefing_date=target_date,
        episode_summaries=summaries,
        recent_briefings=recent,
        client=active_client,
        model=sonnet_model,
    )

    briefing_id = briefing_id_for(target_date)
    structured = {
        "briefing_id": briefing_id,
        "briefing_date": target_date.isoformat(),
        "generated_at": now.isoformat(),
        "model_versions": {"haiku": haiku_model, "sonnet": sonnet_model},
        "episode_count": len(summaries),
        "episodes": summaries,
        "aggregation": aggregation,
    }
    markdown = to_markdown(structured)

    if dry_run:
        return {
            "status": "dry_run",
            "briefing": structured,
            "markdown": markdown,
        }

    with TranscriptStore(str(db_path)) as store:
        store.upsert_briefing(
            briefing_id=briefing_id,
            briefing_date=target_date,
            episode_count=len(summaries),
            structured_json=json.dumps(structured, default=str),
            markdown=markdown,
            generated_at=now,
            model_versions=json.dumps(structured["model_versions"]),
        )

    md_path = write_markdown(markdown, briefing_date=target_date, briefings_dir=briefings_dir)

    summary: dict[str, Any] = {
        "status": "ok",
        "briefing_id": briefing_id,
        "briefing_date": target_date.isoformat(),
        "episode_count": len(summaries),
        "markdown_path": str(md_path),
        "headline": aggregation.get("headline"),
    }

    # Phase 3.7.1 — optional Telegram delivery; degrades to skipped/failed
    # without ever raising, so a delivery hiccup never aborts a real run.
    from .delivery.telegram import send_briefing_to_telegram
    delivery_result = send_briefing_to_telegram(
        briefing_data=structured,
        markdown=markdown,
    )
    log.info("telegram delivery: %s", delivery_result.get("status"))
    summary["telegram"] = delivery_result

    return summary


def _build_anthropic_client() -> Any:
    """Lazy import + construction so importing this module doesn't require
    the anthropic SDK to be installed (tests pass their own stub)."""
    import anthropic  # noqa: WPS433 - lazy by design
    return anthropic.Anthropic()
