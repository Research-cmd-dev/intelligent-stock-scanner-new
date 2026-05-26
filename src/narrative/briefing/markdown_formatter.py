"""Render a briefing's structured JSON to human-readable Markdown.

Pure function over the briefing dict. Idempotent: the same input always
produces the same output, so the Markdown can be regenerated from the
DB row without re-running any LLM call.
"""

from __future__ import annotations

from typing import Any


def to_markdown(briefing: dict[str, Any]) -> str:
    """Build the Markdown view of a briefing dict (§7 layout)."""
    date_s = str(briefing.get("briefing_date") or "")
    generated = str(briefing.get("generated_at") or "")
    ep_count = int(briefing.get("episode_count") or 0)
    models = briefing.get("model_versions") or {}
    haiku = models.get("haiku", "")
    sonnet = models.get("sonnet", "")
    agg = briefing.get("aggregation") or {}
    episodes = briefing.get("episodes") or []

    parts: list[str] = []
    parts.append(f"# Narrative Briefing — {date_s}")
    parts.append(
        f"*Generated {generated} UTC · {ep_count} new episodes · "
        f"models: {haiku}, {sonnet}*"
    )
    parts.append("")

    headline = (agg.get("headline") or "").strip()
    parts.append("## Headline")
    parts.append(headline or "_(no headline produced)_")
    parts.append("")

    parts.append("## Ticker Rollup")
    parts.append(_render_ticker_rollup(agg.get("ticker_rollup") or []))
    parts.append("")

    picks_section = _render_speculative_picks(agg.get("speculative_picks") or [])
    if picks_section:
        parts.append(picks_section)
        parts.append("")

    parts.append("## Episode Highlights")
    for ep in episodes:
        parts.append(_render_episode(ep))
        parts.append("---")
    parts.append("")

    emerging = agg.get("emerging_themes") or []
    if emerging:
        parts.append("## Emerging Themes")
        for t in emerging:
            parts.append(f"- **{t}** — first appearance in corpus this week")
        parts.append("")

    themes_today = agg.get("themes_today") or []
    if themes_today:
        parts.append("## Themes (All)")
        for t in themes_today:
            parts.append(f"- {t}")
        parts.append("")

    firsts = agg.get("notable_firsts") or []
    if firsts:
        parts.append("## Notable Firsts")
        for f in firsts:
            parts.append(f"- {f}")
        parts.append("")

    obs = agg.get("cross_episode_observations") or []
    if obs:
        parts.append("## Cross-Episode Observations")
        for o in obs:
            parts.append(f"- {o}")
        parts.append("")

    parts.append("---")
    parts.append(
        f"*Briefing generated from {ep_count} episodes ingested in the 24h "
        f"window prior to {date_s}T05:30Z. Coverage limited to channels in "
        "config/channels.yaml and speakers in config/speakers.yaml.*"
    )
    return "\n".join(parts) + "\n"


def _render_ticker_rollup(rollup: list[dict[str, Any]]) -> str:
    if not rollup:
        return "_(no tickers mentioned in today's episodes)_"
    lines = [
        "| Symbol | Mentions | Sentiment | Episodes | Direction |",
        "|---|---|---|---|---|",
    ]
    for r in rollup:
        sent = float(r.get("avg_sentiment") or 0.0)
        sent_s = f"{sent:+.2f}"
        lines.append(
            f"| {r.get('symbol', '')} | {int(r.get('total_mentions') or 0)} | "
            f"{sent_s} | {int(r.get('episode_count') or 0)} | "
            f"{r.get('direction', '')} |"
        )
    return "\n".join(lines)


# Per-bucket render caps and bucket definitions (Phase 3.7.3).
_PICK_RENDER_CAP_PER_BUCKET = 5


def _render_speculative_picks(picks: list[dict[str, Any]]) -> str:
    """Render verified picks split into Low Cap / Mid Cap / Above Target.

    Returns empty string to omit the whole section when there's nothing
    actionable to show. Buckets used:
      * Low Cap: pick's actual ``bucket == "low"`` (or unverified pick
        whose ``target_bucket == "low"``)
      * Mid Cap: same for ``"mid"``
      * Above Target: pick's actual ``bucket == "large"`` — surfaced
        rather than dropped so the user sees what got reclassified

    Each pick line carries markers:
      * ``✅`` verified
      * ``❌`` failed verification
      * ``⚠️`` actual bucket differs from target bucket
    """
    if not picks:
        return ""

    low: list[dict[str, Any]] = []
    mid: list[dict[str, Any]] = []
    above: list[dict[str, Any]] = []
    for p in picks:
        bucket = _effective_bucket(p)
        if bucket == "low":
            low.append(p)
        elif bucket == "mid":
            mid.append(p)
        elif bucket == "large":
            above.append(p)
        else:
            # unknown bucket: keep visible in the user's stated target slot
            tgt = (p.get("target_bucket") or "low").lower()
            (mid if tgt == "mid" else low).append(p)

    if not (low or mid or above):
        return ""

    parts: list[str] = [
        "## Speculative Picks — Verified via Web Search",
        "*Tickers derived from today's narrative, verified for existence "
        "and current market cap. Not investment advice — confirm fundamentals "
        "before trading.*",
        "",
    ]

    if low:
        parts.append("### Low Cap ($300M-$2B)")
        for p in low[:_PICK_RENDER_CAP_PER_BUCKET]:
            parts.extend(_render_one_pick(p))
    if mid:
        parts.append("### Mid Cap ($2B-$10B)")
        for p in mid[:_PICK_RENDER_CAP_PER_BUCKET]:
            parts.extend(_render_one_pick(p))
    if above:
        parts.append("### Reclassified Above Target ($10B+) ⚠️")
        for p in above[:_PICK_RENDER_CAP_PER_BUCKET]:
            parts.extend(_render_one_pick(p))

    return "\n".join(parts).rstrip()


def _effective_bucket(pick: dict[str, Any]) -> str:
    """Researcher-assigned bucket if present, else 'unknown'."""
    b = (pick.get("bucket") or "").strip().lower()
    if b in {"low", "mid", "large", "unknown"}:
        return b
    return "unknown"


_DELISTED_SIGNALS: tuple[str, ...] = (
    "delisted",
    "no longer publicly traded",
    "no longer trades",
    "no longer an independent",
    "no longer independent",
    "acquired by",
    "acquisition completed",
    "acquisition complete",
    "merger completed",
    "merger complete",
    "all-stock merger",
    "taken private",
    "private company",
    "ceased trading",
    "wholly-owned subsidiary",
)


def _verification_marker(pick: dict[str, Any]) -> str:
    """Return ✅ for valid picks, ❌ for picks the verifier proved invalid.

    Even when ``verified=True``, if the contradicting-news or verification-
    notes fields surface evidence the company is no longer publicly traded
    (acquired, merged out, taken private), flip to ❌ — the ticker existed
    at some point but isn't a current investable security.
    """
    if not pick.get("verified"):
        return "❌"
    contradicting = (pick.get("recent_news_contradicting") or "").lower()
    notes = (pick.get("verification_notes") or "").lower()
    combined = f"{contradicting} {notes}"
    if any(signal in combined for signal in _DELISTED_SIGNALS):
        return "❌"
    return "✅"


def _render_one_pick(pick: dict[str, Any]) -> list[str]:
    ticker = str(pick.get("ticker") or "").strip().upper() or "?"
    name = str(pick.get("name") or "").strip()
    conviction = str(pick.get("conviction") or "low").strip().lower()
    target_bucket = (pick.get("target_bucket") or "low").lower()
    actual_bucket = _effective_bucket(pick)

    markers: list[str] = []
    if "verified" in pick:
        markers.append(_verification_marker(pick))
    if (
        actual_bucket not in {"unknown", ""}
        and target_bucket not in {"unknown", ""}
        and actual_bucket != target_bucket
    ):
        markers.append("⚠️")
    marker_str = " ".join(markers)

    actual_mcap = pick.get("actual_mcap_billions")
    est_mcap = pick.get("estimated_mcap_billions")
    mcap_str = _format_pick_mcap(actual_mcap, est_mcap)

    head_name = f"{ticker} — {name}" if name else ticker
    header = f"**{head_name}**"
    if marker_str:
        header = f"{header} {marker_str}"
    header = f"{header}  *({mcap_str}, conviction: {conviction})*"

    lines: list[str] = [header]

    thesis = str(pick.get("thesis") or "").strip()
    if thesis:
        lines.append(thesis)

    biz = str(pick.get("actual_business_summary") or "").strip()
    if biz:
        lines.append(f"*Actual business:* {biz}")

    supporting = pick.get("recent_news_supporting")
    if supporting:
        lines.append(f"*Supporting news:* {supporting}")
    contradicting = pick.get("recent_news_contradicting")
    if contradicting:
        lines.append(f"*Contradicting:* {contradicting}")

    footer_bits: list[str] = []
    amplified = pick.get("tickers_amplified_by") or []
    if amplified:
        footer_bits.append(
            "Amplified by: " + ", ".join(str(a).upper() for a in amplified)
        )
    source = str(pick.get("narrative_source") or "").strip()
    if source:
        footer_bits.append(f"Source: {source}")
    notes = str(pick.get("verification_notes") or "").strip()
    if notes:
        footer_bits.append(f"Verification: {notes}")
    if footer_bits:
        lines.append(f"*{' · '.join(footer_bits)}*")

    lines.append("")
    return lines


def _format_pick_mcap(actual: Any, estimated: Any) -> str:
    """Prefer the researcher's actual mcap; fall back to the aggregator's estimate."""
    actual_v = _coerce_float(actual)
    if actual_v is not None:
        return f"actual {_format_billions(actual_v)}"
    est_v = _coerce_float(estimated)
    if est_v is not None:
        return f"~{_format_billions(est_v)} est."
    return "mcap unknown"


def _coerce_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _format_billions(value: float) -> str:
    if value >= 1.0:
        return f"${value:.1f}B"
    return f"${value * 1000:.0f}M"


def _render_episode(ep: dict[str, Any]) -> str:
    title = (ep.get("title") or "").strip()
    channel = (ep.get("channel") or "").strip()
    speakers = ep.get("speakers") or []
    tone = (ep.get("tone") or "").strip()
    summary = (ep.get("summary") or "").strip()
    tickers = ep.get("tickers") or []
    themes = ep.get("themes") or []
    claims = ep.get("notable_claims") or []
    deep_links = ep.get("deep_links") or []

    head = f"### {channel} — {title}" if channel else f"### {title}"
    lines = [head]
    lines.append(
        f"**Speakers**: {', '.join(speakers) if speakers else '_(none tagged)_'}  "
    )
    lines.append(f"**Tone**: {tone or 'neutral'}")
    lines.append("")
    lines.append(summary or "_(no summary)_")
    lines.append("")

    if tickers:
        t_inline = ", ".join(
            f"{t.get('symbol', '')} ({float(t.get('sentiment') or 0.0):+.2f}, "
            f"{int(t.get('mentions') or 0)}x)"
            for t in tickers
        )
        lines.append(f"**Tickers**: {t_inline}  ")
    if themes:
        lines.append(f"**Themes**: {', '.join(themes)}  ")
    if claims:
        for c in claims:
            sp = (c.get("speaker") or "").strip()
            sp_s = f" — _{sp}_" if sp else ""
            lines.append(f"- _\"{(c.get('claim') or '').strip()}\"_{sp_s}")
    if deep_links:
        lines.append("**Deep links**:")
        for url in deep_links:
            lines.append(f"  - {url}")
    return "\n".join(lines)
