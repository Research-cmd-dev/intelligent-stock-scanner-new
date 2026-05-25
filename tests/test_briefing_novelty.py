"""Tests for theme novelty detection.

Pure function — no LLM, no DB. The recent_briefings fixture mimics rows
returned by ``TranscriptStore.list_recent_briefings``.
"""

from __future__ import annotations

import json
from typing import Any

from src.narrative.briefing.novelty_detector import emerging_themes


def _row(themes: list[str], briefing_date: str = "2026-05-23") -> dict[str, Any]:
    return {
        "id": f"briefing_{briefing_date}",
        "briefing_date": briefing_date,
        "structured_json": json.dumps({
            "aggregation": {"themes_today": themes},
        }),
    }


def test_first_appearance_detected() -> None:
    out = emerging_themes(
        todays_themes=["AI capex", "sovereign AI"],
        recent_briefings=[],
    )
    assert out == ["AI capex", "sovereign AI"]


def test_subsequent_appearance_not_emerging() -> None:
    out = emerging_themes(
        todays_themes=["AI capex", "sovereign AI"],
        recent_briefings=[_row(["AI capex"], "2026-05-23")],
    )
    assert out == ["sovereign AI"]


def test_comparison_is_case_insensitive() -> None:
    out = emerging_themes(
        todays_themes=["AI Capex"],
        recent_briefings=[_row(["ai capex"], "2026-05-23")],
    )
    assert out == []


def test_within_14_day_window_input_drives_decision() -> None:
    """The detector trusts its caller for windowing — it doesn't reach
    back into the date column. If the row is supplied, its themes count."""
    # 13 days ago: theme present → not emerging
    out_in_window = emerging_themes(
        todays_themes=["AI capex"],
        recent_briefings=[_row(["AI capex"], "2026-05-11")],
    )
    assert out_in_window == []

    # 15 days ago: caller (TranscriptStore.list_recent_briefings(days=14))
    # would not have supplied this row, so it's not in the input → emerging.
    out_out_of_window = emerging_themes(
        todays_themes=["AI capex"],
        recent_briefings=[],
    )
    assert out_out_of_window == ["AI capex"]


def test_empty_today_returns_empty() -> None:
    assert emerging_themes([], [_row(["AI capex"])]) == []


def test_malformed_prior_briefing_is_ignored() -> None:
    bad = {"id": "briefing_bad", "briefing_date": "2026-05-23",
           "structured_json": "not json"}
    out = emerging_themes(
        todays_themes=["AI capex"],
        recent_briefings=[bad, _row(["AI capex"])],
    )
    assert out == []


def test_preserves_today_ordering() -> None:
    out = emerging_themes(
        todays_themes=["zebra", "apple", "mango"],
        recent_briefings=[],
    )
    assert out == ["zebra", "apple", "mango"]
