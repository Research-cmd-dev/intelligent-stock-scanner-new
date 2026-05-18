"""Integration: Scanner + NarrativeScorer enriches each match."""

from __future__ import annotations

import pandas as pd

import src.scanner.scanner as scanner_mod
from src.narrative import NarrativeScorer
from src.scanner import Scanner, TREND_RIDER
from tests.news_fixtures import (
    FakeSource, bearish_items, bullish_items,
)
from tests.synthetic import trend_rider_series


def _install_fetcher(monkeypatch, frames: dict[str, pd.DataFrame]) -> None:
    def fake_fetch_many(symbols, lookback_days=400):
        return {s: frames[s] for s in symbols if s in frames}
    monkeypatch.setattr(scanner_mod, "fetch_many", fake_fetch_many)


def test_scanner_enriches_matches_with_narrative(monkeypatch) -> None:
    frames = {"BULL": trend_rider_series()}
    _install_fetcher(monkeypatch, frames)

    scorer = NarrativeScorer(
        sources=[FakeSource(bullish_items("BULL"))], use_cache=False,
    )
    scanner = Scanner(min_score=0, narrative_scorer=scorer)
    report = scanner.scan_watchlist(["BULL"])

    assert report.matches
    m = report.matches[0]
    assert m.pattern == TREND_RIDER
    assert m.narrative is not None
    assert m.narrative.score > 0.6
    assert m.composite_score is not None
    # Bullish narrative should *boost* a strong pattern, not drag it down.
    assert m.composite_score > m.score - 5


def test_bearish_narrative_pulls_composite_below_pattern(monkeypatch) -> None:
    frames = {"BEAR": trend_rider_series()}
    _install_fetcher(monkeypatch, frames)

    scorer = NarrativeScorer(
        sources=[FakeSource(bearish_items("BEAR"))], use_cache=False,
    )
    scanner = Scanner(min_score=0, narrative_scorer=scorer)
    report = scanner.scan_watchlist(["BEAR"])

    m = report.matches[0]
    assert m.narrative is not None
    assert m.narrative.score < 0.35
    # Pattern was strong; bearish news should drop composite materially.
    assert m.composite_score < m.score


def test_scanner_without_narrative_keeps_composite_equal_to_score(monkeypatch) -> None:
    frames = {"PURE": trend_rider_series()}
    _install_fetcher(monkeypatch, frames)
    scanner = Scanner(min_score=0)  # no scorer
    report = scanner.scan_watchlist(["PURE"])
    m = report.matches[0]
    assert m.narrative is None
    assert m.composite_score == m.score
    assert m.effective_score == m.score


def test_scanner_sorts_by_composite_score(monkeypatch) -> None:
    """Two symbols with identical pattern frames; news should determine rank."""
    # Same seed -> identical pattern scores; only narrative differs.
    base = trend_rider_series()
    frames = {"BULL": base, "BEAR": base}
    _install_fetcher(monkeypatch, frames)

    class PerSymbol:
        """Returns bullish items for BULL, bearish for BEAR."""
        name = "per_symbol"
        def fetch(self, symbol, *, limit=20):
            return bullish_items(symbol) if symbol == "BULL" else bearish_items(symbol)

    scanner = Scanner(min_score=0, narrative_scorer=NarrativeScorer(
        sources=[PerSymbol()], use_cache=False,
    ))
    report = scanner.scan_watchlist(["BULL", "BEAR"])
    by_symbol = {m.symbol: m for m in report.matches}
    # Pattern scores are equal by construction; composites must diverge.
    assert by_symbol["BULL"].score == by_symbol["BEAR"].score
    assert by_symbol["BULL"].composite_score > by_symbol["BEAR"].composite_score
    # And the sort order on effective_score must reflect that.
    symbols_in_order = [m.symbol for m in report.matches]
    assert symbols_in_order.index("BULL") < symbols_in_order.index("BEAR")


def test_narrative_failure_does_not_abort_scan(monkeypatch) -> None:
    frames = {"FLAKY": trend_rider_series()}
    _install_fetcher(monkeypatch, frames)

    class BrokenScorer:
        def score(self, ticker):
            raise RuntimeError("boom")

    scanner = Scanner(min_score=0, narrative_scorer=BrokenScorer())
    report = scanner.scan_watchlist(["FLAKY"])
    # Pattern still surfaces; no narrative attached.
    assert report.matches
    assert report.matches[0].narrative is None


def test_report_to_dataframe_includes_narrative_columns(monkeypatch) -> None:
    frames = {"BULL": trend_rider_series()}
    _install_fetcher(monkeypatch, frames)
    scorer = NarrativeScorer(
        sources=[FakeSource(bullish_items("BULL"))], use_cache=False,
    )
    scanner = Scanner(min_score=0, narrative_scorer=scorer)
    table = scanner.scan_watchlist(["BULL"]).to_dataframe()
    for col in ("composite_score", "narrative_score", "narrative_explanation"):
        assert col in table.columns
