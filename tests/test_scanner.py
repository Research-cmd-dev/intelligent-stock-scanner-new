"""Tests for the Scanner orchestrator using a stubbed fetcher.

We monkey-patch ``src.scanner.scanner.fetch_many`` to feed engineered
synthetic frames in — this exercises the full pipeline without any
network I/O.
"""

from __future__ import annotations

import pandas as pd

import src.scanner.scanner as scanner_mod
from src.scanner import BOTTOM_HUNTER, TREND_RIDER, Scanner
from tests.synthetic import (
    bottom_hunter_series, flat_chop_series, trend_rider_series,
)


def _install_fetcher(monkeypatch, frames: dict[str, pd.DataFrame]) -> None:
    def fake_fetch_many(symbols, lookback_days=400):
        return {s: frames[s] for s in symbols if s in frames}
    monkeypatch.setattr(scanner_mod, "fetch_many", fake_fetch_many)


def test_scanner_finds_both_patterns_in_mixed_universe(monkeypatch) -> None:
    frames = {
        "UPCO": trend_rider_series(),
        "DNCO": bottom_hunter_series(),
        "CHOP": flat_chop_series(),
    }
    _install_fetcher(monkeypatch, frames)

    report = Scanner(min_score=0).scan_watchlist(list(frames))
    patterns = {m.pattern for m in report.matches}
    assert TREND_RIDER in patterns
    assert BOTTOM_HUNTER in patterns

    symbols_by_pattern = {
        m.pattern: m.symbol for m in report.matches
    }
    assert symbols_by_pattern[TREND_RIDER] == "UPCO"
    assert symbols_by_pattern[BOTTOM_HUNTER] == "DNCO"

    # Coverage counters should reflect the run.
    assert report.coverage["universe"] == 3
    assert report.coverage["fetched"] == 3
    assert report.coverage["matches"] >= 2


def test_scanner_sorts_matches_by_score_desc(monkeypatch) -> None:
    frames = {
        "UPCO": trend_rider_series(),
        "DNCO": bottom_hunter_series(),
    }
    _install_fetcher(monkeypatch, frames)
    report = Scanner(min_score=0).scan_watchlist(list(frames))
    scores = [m.score for m in report.matches]
    assert scores == sorted(scores, reverse=True)


def test_scanner_respects_min_score(monkeypatch) -> None:
    frames = {"UPCO": trend_rider_series(), "DNCO": bottom_hunter_series()}
    _install_fetcher(monkeypatch, frames)
    report = Scanner(min_score=999).scan_watchlist(list(frames))
    assert report.matches == []


def test_scan_frame_works_without_fetcher() -> None:
    df = trend_rider_series()
    matches = Scanner().scan_frame(df, "UPCO")
    assert any(m.pattern == TREND_RIDER for m in matches)


def test_report_to_dataframe(monkeypatch) -> None:
    frames = {"UPCO": trend_rider_series()}
    _install_fetcher(monkeypatch, frames)
    report = Scanner(min_score=0).scan_watchlist(["UPCO"])
    table = report.to_dataframe()
    assert not table.empty
    assert "score" in table.columns
    assert "pattern" in table.columns
