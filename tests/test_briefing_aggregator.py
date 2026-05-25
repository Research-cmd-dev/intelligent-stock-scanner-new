"""Tests for the ticker rollup math and the daily aggregator wiring.

The math tests (rollup) are pure functions — no LLM. The aggregator
tests use the same mock-client harness as the summarizer tests.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from src.narrative.briefing.llm_aggregator import aggregate_daily
from src.narrative.briefing.ticker_aggregator import rollup_tickers


# --------------------------------------------------------------------- #
# Mock client (same shape as the summarizer tests)                      #
# --------------------------------------------------------------------- #


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_TextBlock(text)]


class _Messages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        out = self._responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return _Response(out)


class _Client:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _Messages(responses)


# --------------------------------------------------------------------- #
# Ticker rollup math                                                    #
# --------------------------------------------------------------------- #


def _episode(ep_id: str, tickers: list[dict[str, Any]]) -> dict[str, Any]:
    return {"episode_id": ep_id, "tickers": tickers}


def test_ticker_rollup_sums_mentions_across_episodes() -> None:
    eps = [
        _episode("e1", [{"symbol": "NVDA", "sentiment": 0.5, "mentions": 3}]),
        _episode("e2", [{"symbol": "NVDA", "sentiment": 0.4, "mentions": 2}]),
        _episode("e3", [{"symbol": "NVDA", "sentiment": 0.6, "mentions": 5}]),
    ]
    rollup = rollup_tickers(eps)
    nvda = next(r for r in rollup if r["symbol"] == "NVDA")
    assert nvda["total_mentions"] == 10
    assert nvda["episode_count"] == 3


def test_ticker_rollup_averages_sentiment_weighted_by_mentions() -> None:
    # 5 mentions at +0.8, 2 at -0.4 → weighted mean = (4.0 + -0.8) / 7 ≈ 0.457
    eps = [
        _episode("e1", [{"symbol": "META", "sentiment": 0.8, "mentions": 5}]),
        _episode("e2", [{"symbol": "META", "sentiment": -0.4, "mentions": 2}]),
    ]
    rollup = rollup_tickers(eps)
    meta = next(r for r in rollup if r["symbol"] == "META")
    assert meta["total_mentions"] == 7
    assert abs(meta["avg_sentiment"] - 0.457) < 0.01
    assert meta["direction"] == "bullish_dominant"


def test_ticker_rollup_direction_labels() -> None:
    eps = [
        _episode("e1", [{"symbol": "BULL", "sentiment": 0.7, "mentions": 5}]),
        _episode("e2", [{"symbol": "BEAR", "sentiment": -0.6, "mentions": 4}]),
        _episode("e3", [{"symbol": "FLAT", "sentiment": 0.0, "mentions": 2}]),
        _episode("e4", [{"symbol": "MIX",  "sentiment": 0.1, "mentions": 3}]),
    ]
    rollup = {r["symbol"]: r for r in rollup_tickers(eps)}
    assert rollup["BULL"]["direction"] == "bullish_dominant"
    assert rollup["BEAR"]["direction"] == "bearish_dominant"
    assert rollup["FLAT"]["direction"] == "neutral"
    assert rollup["MIX"]["direction"] == "mixed"


def test_ticker_rollup_sorts_by_mentions_desc() -> None:
    eps = [
        _episode("e1", [
            {"symbol": "AAA", "sentiment": 0.5, "mentions": 1},
            {"symbol": "BBB", "sentiment": 0.5, "mentions": 5},
        ]),
    ]
    rollup = rollup_tickers(eps)
    assert [r["symbol"] for r in rollup] == ["BBB", "AAA"]


def test_ticker_rollup_empty_input() -> None:
    assert rollup_tickers([]) == []
    assert rollup_tickers([_episode("e1", [])]) == []


def test_ticker_rollup_normalizes_case() -> None:
    eps = [
        _episode("e1", [{"symbol": "nvda", "sentiment": 0.5, "mentions": 1}]),
        _episode("e2", [{"symbol": "NVDA", "sentiment": 0.5, "mentions": 1}]),
    ]
    rollup = rollup_tickers(eps)
    assert len(rollup) == 1
    assert rollup[0]["symbol"] == "NVDA"
    assert rollup[0]["total_mentions"] == 2


# --------------------------------------------------------------------- #
# aggregate_daily wiring                                                #
# --------------------------------------------------------------------- #


def _sonnet_json(themes: list[str] | None = None) -> str:
    return json.dumps({
        "headline": "Capex thesis dominates today",
        "themes_today": themes or ["AI capex", "sovereign AI"],
        "notable_firsts": ["Gerstner first uses 'capex saturation' this quarter"],
        "cross_episode_observations": [
            "Two speakers used 'data-dependent' framing in 24h",
        ],
    })


def test_aggregate_daily_uses_deterministic_rollup() -> None:
    client = _Client([_sonnet_json()])
    eps = [
        _episode("e1", [{"symbol": "NVDA", "sentiment": 0.8, "mentions": 5}]),
        _episode("e2", [{"symbol": "NVDA", "sentiment": 0.6, "mentions": 3}]),
    ]
    out = aggregate_daily(
        briefing_date=date(2026, 5, 24),
        episode_summaries=eps,
        recent_briefings=[],
        client=client,
    )
    assert out["headline"] == "Capex thesis dominates today"
    assert len(out["ticker_rollup"]) == 1
    assert out["ticker_rollup"][0]["symbol"] == "NVDA"
    assert out["ticker_rollup"][0]["total_mentions"] == 8


def test_aggregate_daily_computes_emerging_themes() -> None:
    client = _Client([_sonnet_json(themes=["AI capex", "NEW THEME"])])
    prior = [{
        "structured_json": json.dumps({
            "aggregation": {"themes_today": ["AI capex"]},
        }),
    }]
    out = aggregate_daily(
        briefing_date=date(2026, 5, 24),
        episode_summaries=[],
        recent_briefings=prior,
        client=client,
    )
    assert out["emerging_themes"] == ["NEW THEME"]


def test_aggregate_daily_stub_on_double_failure() -> None:
    client = _Client(["nonsense 1", "nonsense 2"])
    out = aggregate_daily(
        briefing_date=date(2026, 5, 24),
        episode_summaries=[],
        recent_briefings=[],
        client=client,
    )
    assert "failed" in out["headline"].lower()
    assert out["ticker_rollup"] == []
    assert out["themes_today"] == []
