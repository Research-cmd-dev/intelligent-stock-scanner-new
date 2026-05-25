"""Tests for the Telegram delivery layer. All requests.post calls are mocked."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.narrative.briefing.delivery.telegram import (
    _build_headline_message,
    send_briefing_to_telegram,
)


def _briefing(**overrides: Any) -> dict[str, Any]:
    base = {
        "briefing_id": "briefing_2026-05-24",
        "briefing_date": "2026-05-24",
        "episode_count": 7,
        "aggregation": {
            "headline": "Jensen dominates the day; Powell pushes back",
            "ticker_rollup": [
                {"symbol": "NVDA", "total_mentions": 96, "avg_sentiment": 0.89,
                 "episode_count": 3, "direction": "bullish_dominant"},
                {"symbol": "AAPL", "total_mentions": 26, "avg_sentiment": 0.12,
                 "episode_count": 2, "direction": "mixed"},
            ],
            "emerging_themes": ["CUDA lock-in", "China chip policy"],
        },
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------- #
# 1. Happy path — both API calls succeed                                #
# --------------------------------------------------------------------- #


def test_send_briefing_success() -> None:
    msg_resp = MagicMock(spec=requests.Response)
    msg_resp.json.return_value = {"ok": True, "result": {"message_id": 101}}
    msg_resp.raise_for_status.return_value = None

    doc_resp = MagicMock(spec=requests.Response)
    doc_resp.json.return_value = {"ok": True, "result": {"message_id": 102}}
    doc_resp.raise_for_status.return_value = None

    with patch("src.narrative.briefing.delivery.telegram.requests.post",
               side_effect=[msg_resp, doc_resp]) as mock_post:
        result = send_briefing_to_telegram(
            briefing_data=_briefing(),
            markdown="# Briefing\n\nContent.",
            bot_token="test-token",
            channel_id="-1003000000",
        )

    assert result["status"] == "sent"
    assert result["headline_message_id"] == 101
    assert result["document_message_id"] == 102
    assert mock_post.call_count == 2
    # First call is sendMessage; second is sendDocument.
    assert "sendMessage" in mock_post.call_args_list[0].args[0]
    assert "sendDocument" in mock_post.call_args_list[1].args[0]


# --------------------------------------------------------------------- #
# 2. No credentials → skipped, no HTTP                                  #
# --------------------------------------------------------------------- #


def test_send_briefing_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)
    with patch("src.narrative.briefing.delivery.telegram.requests.post") as mock_post:
        result = send_briefing_to_telegram(
            briefing_data=_briefing(),
            markdown="# Briefing",
        )
    assert result == {"status": "skipped", "reason": "no_credentials"}
    mock_post.assert_not_called()


def test_send_briefing_only_token_set_still_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)
    with patch("src.narrative.briefing.delivery.telegram.requests.post") as mock_post:
        result = send_briefing_to_telegram(
            briefing_data=_briefing(), markdown="# Briefing",
        )
    assert result["status"] == "skipped"
    mock_post.assert_not_called()


# --------------------------------------------------------------------- #
# 3. API failure → status=failed, error string, never raises            #
# --------------------------------------------------------------------- #


def test_send_briefing_api_failure() -> None:
    with patch("src.narrative.briefing.delivery.telegram.requests.post",
               side_effect=requests.ConnectionError("network down")):
        result = send_briefing_to_telegram(
            briefing_data=_briefing(),
            markdown="# Briefing",
            bot_token="x",
            channel_id="y",
        )
    assert result["status"] == "failed"
    assert "network down" in result["error"]


def test_send_briefing_doc_upload_failure_after_message_sent() -> None:
    """If the topline lands but the file upload fails, we still report failed."""
    msg_resp = MagicMock(spec=requests.Response)
    msg_resp.json.return_value = {"ok": True, "result": {"message_id": 101}}
    msg_resp.raise_for_status.return_value = None
    with patch("src.narrative.briefing.delivery.telegram.requests.post",
               side_effect=[msg_resp, requests.HTTPError("413 too large")]):
        result = send_briefing_to_telegram(
            briefing_data=_briefing(),
            markdown="# Briefing",
            bot_token="x",
            channel_id="y",
        )
    assert result["status"] == "failed"
    assert "413 too large" in result["error"]


# --------------------------------------------------------------------- #
# Headline rendering helpers                                            #
# --------------------------------------------------------------------- #


def test_headline_includes_top_5_tickers_and_emerging_themes() -> None:
    msg = _build_headline_message(_briefing())
    assert "NVDA" in msg and "AAPL" in msg
    assert "CUDA lock-in" in msg
    assert "Jensen dominates" in msg
    # Direction arrows
    assert "↑" in msg


def test_headline_escapes_html_in_user_text() -> None:
    b = _briefing(aggregation={
        "headline": "AT&T <leaks> news",
        "ticker_rollup": [],
        "emerging_themes": ["<script>x</script>"],
    })
    msg = _build_headline_message(b)
    assert "&amp;" in msg
    assert "&lt;script&gt;" in msg
    # Original < / > / & must not survive raw.
    assert "<script>" not in msg


def test_headline_truncates_oversize_content() -> None:
    huge = "x" * 10_000
    b = _briefing(aggregation={"headline": huge, "ticker_rollup": [], "emerging_themes": []})
    msg = _build_headline_message(b)
    assert len(msg) <= 4000
    assert msg.endswith("[truncated]")
