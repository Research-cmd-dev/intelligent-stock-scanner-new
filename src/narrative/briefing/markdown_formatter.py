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


# Picks above this mcap (in billions) render with an ⚠️ flag — the
# constraint in the prompt asks for <$10B, so anything bigger is the
# LLM ignoring the target and worth surfacing rather than hiding.
_PICK_MCAP_TARGET_B = 10.0
_PICK_RENDER_CAP = 5


def _render_speculative_picks(picks: list[dict[str, Any]]) -> str:
    """Render the picks section, or empty string to omit the section entirely."""
    if not picks:
        return ""
    parts: list[str] = [
        "## Speculative Low-Cap Picks",
        "*Research starters derived from today's narrative. <$10B market cap "
        "target. Not investment advice — verify market cap, business model, "
        "and recent fundamentals before trading.*",
        "",
    ]
    for pick in picks[:_PICK_RENDER_CAP]:
        ticker = str(pick.get("ticker") or "").strip().upper() or "?"
        name = str(pick.get("name") or "").strip()
        mcap = pick.get("estimated_mcap_billions")
        conviction = str(pick.get("conviction") or "low").strip().lower()
        thesis = str(pick.get("thesis") or "").strip()
        source = str(pick.get("narrative_source") or "").strip()
        amplified = pick.get("tickers_amplified_by") or []

        mcap_str = _format_mcap(mcap)
        header_bits: list[str] = []
        if name:
            header_bits.append(f"**{ticker} — {name}**")
        else:
            header_bits.append(f"**{ticker}**")
        header_bits.append(f"*({mcap_str}, conviction: {conviction})*")
        parts.append("  ".join(header_bits))

        if thesis:
            parts.append(thesis)

        footer_bits: list[str] = []
        if amplified:
            footer_bits.append(
                "Amplified by: " + ", ".join(str(a).upper() for a in amplified)
            )
        if source:
            footer_bits.append(f"Source: {source}")
        if footer_bits:
            parts.append(f"*{' · '.join(footer_bits)}*")
        parts.append("")

    parts.append(
        "*If a pick's estimated mcap looks higher than expected, the LLM may "
        "have miscategorized — verify before considering. Picks with conviction "
        "\"low\" should be treated as conversation starters, not signals.*"
    )
    return "\n".join(parts)


def _format_mcap(mcap: Any) -> str:
    """``~$30B est. mcap`` with ⚠️ above target flag when over the threshold."""
    try:
        value = float(mcap) if mcap is not None else None
    except (TypeError, ValueError):
        value = None
    if value is None:
        return "mcap unknown"
    if value >= 1.0:
        base = f"~${value:.0f}B est. mcap"
    else:
        base = f"~${value * 1000:.0f}M est. mcap"
    if value > _PICK_MCAP_TARGET_B:
        return f"{base} ⚠️ above target"
    return base


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
