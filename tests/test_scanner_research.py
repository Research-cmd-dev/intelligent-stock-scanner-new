"""Integration: Scanner + Researcher wiring.

Pins the contract documented in CLAUDE.md:

- Default behavior is unchanged (`researcher=None` → `match.research` stays
  None on every match, no calls to .research()).
- Research only fires on matches whose `effective_score` clears
  ``DEFAULT_CONVICTION_THRESHOLD`` (70.0).
- The number of `.research()` calls per scan is capped at
  ``research_limit`` **unique symbols** — a symbol hitting multiple
  patterns consumes exactly one slot.
- Each `.research()` call gets a unique symbol; the resulting
  ResearchResult is attached to every match for that symbol.
- A researcher exception on one symbol is logged and skipped; the
  match keeps `research=None`. Never aborts the scan.
- Research enrichment does NOT re-order the result list — ranking is
  finalized before research runs.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
import pytest

import src.scanner.scanner as scanner_mod
from src.research import DEFAULT_CONVICTION_THRESHOLD, ResearchResult
from src.scanner import BOTTOM_HUNTER, TREND_RIDER, MatchResult, Scanner
from tests.synthetic import bottom_hunter_series, trend_rider_series


def _install_fetcher(monkeypatch, frames: dict[str, pd.DataFrame]) -> None:
    def fake_fetch_many(symbols, lookback_days=400):
        return {s: frames[s] for s in symbols if s in frames}
    monkeypatch.setattr(scanner_mod, "fetch_many", fake_fetch_many)


class _RecordingResearcher:
    """Returns a deterministic ResearchResult per ticker and records calls."""

    name = "recording"

    def __init__(self, confidence: float = 0.8) -> None:
        self.calls: list[str] = []
        self.confidence = confidence

    def research(self, ticker: str) -> ResearchResult:
        self.calls.append(ticker)
        return ResearchResult(
            ticker=ticker.upper(),
            as_of=datetime.now(tz=timezone.utc),
            summary=f"deterministic read for {ticker}",
            company_quality="moat: synthetic",
            management="founder-led (test fixture)",
            partnerships="anchor: pytest",
            financial_health="net cash",
            key_risks="this is a test",
            sources=("https://test/" + ticker.lower(),),
            confidence=self.confidence,
        )


class _ExplodingResearcher:
    name = "exploding"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def research(self, ticker: str) -> ResearchResult:
        self.calls.append(ticker)
        raise RuntimeError(f"simulated outage on {ticker}")


# --------------------------------------------------------------------- #
# Backward compatibility — default behavior unchanged                   #
# --------------------------------------------------------------------- #


def test_default_scanner_attaches_no_research(monkeypatch) -> None:
    """With no researcher wired, match.research is None on every match.

    Locks in the "opt-in only" guarantee. If someone adds a default
    researcher to Scanner.__init__, this test fires.
    """
    frames = {"UPCO": trend_rider_series(), "DNCO": bottom_hunter_series()}
    _install_fetcher(monkeypatch, frames)
    report = Scanner(min_score=0).scan_watchlist(list(frames))
    assert report.matches
    assert all(m.research is None for m in report.matches)


# --------------------------------------------------------------------- #
# Happy path — research fires on high-conviction matches                #
# --------------------------------------------------------------------- #


def test_research_fires_on_high_conviction_matches(monkeypatch) -> None:
    frames = {"UPCO": trend_rider_series()}
    _install_fetcher(monkeypatch, frames)
    researcher = _RecordingResearcher()

    # min_score=0 to surface the match; the conviction gate is independent
    # of min_score and is what controls research firing.
    report = Scanner(min_score=0, researcher=researcher).scan_watchlist(["UPCO"])

    assert report.matches
    top = report.matches[0]
    if top.effective_score >= DEFAULT_CONVICTION_THRESHOLD:
        assert researcher.calls == ["UPCO"]
        assert top.research is not None
        assert top.research.ticker == "UPCO"
        assert top.research.summary == "deterministic read for UPCO"
        assert top.research.confidence == pytest.approx(0.8)
    else:
        # Synthetic series scored below 70 — research should NOT have
        # fired. The wiring still must be correct in that direction.
        assert researcher.calls == []
        assert top.research is None


# --------------------------------------------------------------------- #
# Conviction gate — sub-threshold matches don't get research            #
# --------------------------------------------------------------------- #


def test_research_does_not_fire_below_conviction_threshold(monkeypatch) -> None:
    """Manually construct sub-threshold matches via scan_frame's surrogate.

    We can't easily force a pattern's effective_score in a discovery
    scan, so we exercise the gate directly by calling the private
    enrichment with synthetic matches.
    """
    researcher = _RecordingResearcher()
    scanner = Scanner(researcher=researcher)

    weak_matches = [
        MatchResult(
            symbol="WEAK",
            pattern=TREND_RIDER,
            score=55.0,
            as_of=pd.Timestamp("2026-05-19"),
            price=10.0,
            composite_score=55.0,
        ),
        MatchResult(
            symbol="MEH",
            pattern=BOTTOM_HUNTER,
            score=60.0,
            as_of=pd.Timestamp("2026-05-19"),
            price=20.0,
            composite_score=60.0,
        ),
    ]
    out = scanner._enrich_with_research(weak_matches)

    assert researcher.calls == []  # nothing above threshold
    assert all(m.research is None for m in out)


# --------------------------------------------------------------------- #
# Top-N cap (unique symbols)                                            #
# --------------------------------------------------------------------- #


def test_research_caps_at_research_limit_unique_symbols() -> None:
    researcher = _RecordingResearcher()
    scanner = Scanner(researcher=researcher, research_limit=3)

    # 6 high-conviction symbols, scores 95..70. Only top 3 by score
    # should get researched.
    matches = [
        MatchResult(
            symbol=sym,
            pattern=TREND_RIDER,
            score=score,
            as_of=pd.Timestamp("2026-05-19"),
            price=10.0,
            composite_score=score,
        )
        for sym, score in [
            ("A", 95.0), ("B", 90.0), ("C", 85.0),
            ("D", 80.0), ("E", 75.0), ("F", 70.0),
        ]
    ]
    out = scanner._enrich_with_research(matches)

    assert researcher.calls == ["A", "B", "C"]
    researched = {m.symbol: m.research for m in out}
    assert researched["A"] is not None
    assert researched["B"] is not None
    assert researched["C"] is not None
    assert researched["D"] is None
    assert researched["E"] is None
    assert researched["F"] is None


def test_research_dedups_per_symbol_across_patterns() -> None:
    """A symbol hitting two patterns consumes exactly one research slot."""
    researcher = _RecordingResearcher()
    scanner = Scanner(researcher=researcher, research_limit=3)

    matches = [
        MatchResult(symbol="A", pattern=TREND_RIDER, score=95.0,
                    as_of=pd.Timestamp("2026-05-19"), price=10.0,
                    composite_score=95.0),
        MatchResult(symbol="A", pattern=BOTTOM_HUNTER, score=93.0,
                    as_of=pd.Timestamp("2026-05-19"), price=10.0,
                    composite_score=93.0),  # same symbol, second pattern
        MatchResult(symbol="B", pattern=TREND_RIDER, score=88.0,
                    as_of=pd.Timestamp("2026-05-19"), price=20.0,
                    composite_score=88.0),
        MatchResult(symbol="C", pattern=TREND_RIDER, score=82.0,
                    as_of=pd.Timestamp("2026-05-19"), price=30.0,
                    composite_score=82.0),
    ]
    out = scanner._enrich_with_research(matches)

    # A is called once, not twice — research_limit slots go to A, B, C
    # (the three unique symbols), not to A, A, B.
    assert researcher.calls == ["A", "B", "C"]
    # Both A matches receive the same research payload — research is
    # per-symbol, not per-pattern.
    a_matches = [m for m in out if m.symbol == "A"]
    assert len(a_matches) == 2
    assert a_matches[0].research is not None
    assert a_matches[1].research is not None
    assert a_matches[0].research.summary == a_matches[1].research.summary


# --------------------------------------------------------------------- #
# Fail-soft contract                                                    #
# --------------------------------------------------------------------- #


def test_researcher_failure_on_one_symbol_does_not_abort_scan() -> None:
    """An exploding researcher logs + skips; other matches stay healthy."""
    researcher = _ExplodingResearcher()
    scanner = Scanner(researcher=researcher, research_limit=5)

    matches = [
        MatchResult(symbol="OOPS", pattern=TREND_RIDER, score=90.0,
                    as_of=pd.Timestamp("2026-05-19"), price=10.0,
                    composite_score=90.0),
        MatchResult(symbol="FINE", pattern=TREND_RIDER, score=85.0,
                    as_of=pd.Timestamp("2026-05-19"), price=20.0,
                    composite_score=85.0),
    ]
    out = scanner._enrich_with_research(matches)

    assert len(researcher.calls) == 2  # both were attempted
    # No payloads attached because both calls raised — but the match
    # objects themselves came through unscathed.
    assert all(m.research is None for m in out)
    assert {m.symbol for m in out} == {"OOPS", "FINE"}


def test_partial_researcher_failure_attaches_what_succeeded() -> None:
    """Only the symbol that raised is skipped; the others get research."""
    class _Mixed:
        name = "mixed"
        def __init__(self) -> None:
            self.calls: list[str] = []
        def research(self, ticker: str) -> ResearchResult:
            self.calls.append(ticker)
            if ticker == "B":
                raise RuntimeError("just B")
            return ResearchResult(
                ticker=ticker, as_of=datetime.now(tz=timezone.utc),
                summary=f"ok {ticker}", confidence=0.7,
            )

    researcher = _Mixed()
    scanner = Scanner(researcher=researcher, research_limit=5)
    matches = [
        MatchResult(symbol="A", pattern=TREND_RIDER, score=90.0,
                    as_of=pd.Timestamp("2026-05-19"), price=10.0,
                    composite_score=90.0),
        MatchResult(symbol="B", pattern=TREND_RIDER, score=85.0,
                    as_of=pd.Timestamp("2026-05-19"), price=20.0,
                    composite_score=85.0),
        MatchResult(symbol="C", pattern=TREND_RIDER, score=80.0,
                    as_of=pd.Timestamp("2026-05-19"), price=30.0,
                    composite_score=80.0),
    ]
    out = scanner._enrich_with_research(matches)
    by_sym = {m.symbol: m for m in out}
    assert by_sym["A"].research is not None
    assert by_sym["B"].research is None  # failed — left untouched
    assert by_sym["C"].research is not None


# --------------------------------------------------------------------- #
# Ranking is finalized before research                                  #
# --------------------------------------------------------------------- #


def test_research_does_not_re_order_matches() -> None:
    """Research enrichment must preserve effective_score order."""
    researcher = _RecordingResearcher()
    scanner = Scanner(researcher=researcher, research_limit=2)

    matches = [
        MatchResult(symbol="TOP", pattern=TREND_RIDER, score=95.0,
                    as_of=pd.Timestamp("2026-05-19"), price=10.0,
                    composite_score=95.0),
        MatchResult(symbol="MID", pattern=TREND_RIDER, score=85.0,
                    as_of=pd.Timestamp("2026-05-19"), price=20.0,
                    composite_score=85.0),
        MatchResult(symbol="LOW", pattern=TREND_RIDER, score=72.0,
                    as_of=pd.Timestamp("2026-05-19"), price=30.0,
                    composite_score=72.0),
    ]
    out = scanner._enrich_with_research(matches)
    assert [m.symbol for m in out] == ["TOP", "MID", "LOW"]


# --------------------------------------------------------------------- #
# MatchResult.to_row() exposes research columns                         #
# --------------------------------------------------------------------- #


def test_to_row_includes_research_columns_when_present() -> None:
    match = MatchResult(
        symbol="A", pattern=TREND_RIDER, score=90.0,
        as_of=pd.Timestamp("2026-05-19"), price=10.0,
        composite_score=90.0,
    )
    # Without research → keys exist with empty defaults so display code
    # can render uniformly without branching.
    row = match.to_row()
    assert "research_summary" in row
    assert "research_confidence" in row
    assert row["research_summary"] == ""
    assert row["research_confidence"] is None

    # With research → keys carry the payload.
    enriched = replace(match, research=ResearchResult(
        ticker="A", as_of=datetime.now(tz=timezone.utc),
        summary="strong moat", confidence=0.75,
    ))
    row2 = enriched.to_row()
    assert row2["research_summary"] == "strong moat"
    assert row2["research_confidence"] == pytest.approx(0.75)
