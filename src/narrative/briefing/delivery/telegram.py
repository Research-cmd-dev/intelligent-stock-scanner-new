"""Telegram Bot API delivery for daily briefings.

Two-message pattern per briefing:
  1. A short HTML-formatted headline message with ticker rollup + emerging themes
  2. The full Markdown briefing as a .md file attachment

Graceful degradation: if ``TELEGRAM_BOT_TOKEN`` or ``TELEGRAM_CHANNEL_ID``
isn't in the environment, delivery is silently skipped (``status="skipped"``).
Failures of the API itself are caught and returned in the status — never raised.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests  # type: ignore[import-untyped]


log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4000  # 4096 ceiling minus margin


def send_briefing_to_telegram(
    *,
    briefing_data: dict[str, Any],
    markdown: str,
    bot_token: str | None = None,
    channel_id: str | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Send briefing to the configured Telegram channel.

    Reads credentials from arguments or env vars ``TELEGRAM_BOT_TOKEN`` /
    ``TELEGRAM_CHANNEL_ID``. Returns a status dict; never raises.
    """
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = channel_id or os.environ.get("TELEGRAM_CHANNEL_ID")

    if not bot_token or not channel_id:
        return {"status": "skipped", "reason": "no_credentials"}

    try:
        headline = _build_headline_message(briefing_data)
        msg_result = _send_message(bot_token, channel_id, headline, timeout_s)
        doc_result = _send_document(
            bot_token,
            channel_id,
            markdown,
            filename=f"briefing_{briefing_data.get('briefing_date', 'unknown')}.md",
            timeout_s=timeout_s,
        )
        return {
            "status": "sent",
            "headline_message_id": (msg_result.get("result") or {}).get("message_id"),
            "document_message_id": (doc_result.get("result") or {}).get("message_id"),
        }
    except Exception as exc:
        log.warning("telegram delivery failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


def _build_headline_message(briefing_data: dict[str, Any]) -> str:
    """HTML-formatted topline that fits in MAX_MESSAGE_CHARS."""
    agg = briefing_data.get("aggregation") or {}
    date_str = str(briefing_data.get("briefing_date") or "")
    episode_count = int(briefing_data.get("episode_count") or 0)

    lines: list[str] = [
        f"<b>📰 Narrative Briefing — {date_str}</b>",
        f"<i>{episode_count} new episodes</i>",
        "",
    ]

    headline = agg.get("headline")
    if headline:
        lines.extend([f"<b>{_escape_html(str(headline))}</b>", ""])

    rollup = (agg.get("ticker_rollup") or [])[:5]
    if rollup:
        lines.append("<b>Top Tickers</b>")
        for r in rollup:
            sym = str(r.get("symbol", ""))
            mentions = int(r.get("total_mentions") or 0)
            sent = float(r.get("avg_sentiment") or 0.0)
            direction = str(r.get("direction", ""))
            arrow = "↑" if sent > 0.3 else "↓" if sent < -0.3 else "→"
            lines.append(
                f"  <code>{sym}</code> {arrow}  {mentions} mentions  "
                f"({sent:+.2f}, {direction})"
            )
        lines.append("")

    emerging = agg.get("emerging_themes") or []
    if emerging:
        lines.append("<b>🆕 Emerging Themes</b>")
        for theme in emerging[:5]:
            lines.append(f"  • {_escape_html(str(theme))}")
        lines.append("")

    lines.append("<i>Full briefing attached ↓</i>")

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 20] + "\n...[truncated]"
    return message


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _send_message(
    bot_token: str, chat_id: str, text: str, timeout_s: float
) -> dict[str, Any]:
    url = f"{API_BASE}/bot{bot_token}/sendMessage"
    response = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=timeout_s,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def _send_document(
    bot_token: str,
    chat_id: str,
    content: str,
    filename: str,
    timeout_s: float,
) -> dict[str, Any]:
    url = f"{API_BASE}/bot{bot_token}/sendDocument"
    files = {"document": (filename, content.encode("utf-8"), "text/markdown")}
    data = {"chat_id": chat_id}
    response = requests.post(url, data=data, files=files, timeout=timeout_s)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload
