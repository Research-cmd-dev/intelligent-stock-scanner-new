"""Protocol + result type for the deep-research layer.

Shape designed to match the mission's stated checklist: *company quality,
management track record, partnerships, financial health, key risks*. Each
slot is a free-form string so the first implementations (LLM summary,
analyst note) can populate it without forcing a schema; later
implementations can layer in structured sub-fields if a richer table view
emerges.

This module deliberately ships no real research implementation — it
defines the contract so the scanner can be wired to it later without
churning call sites. :class:`NullResearcher` exists so consumers can
opt in to the layer unconditionally during the wiring phase and get a
clean "no research yet" result back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Protocol, runtime_checkable

# A composite score at or above this floor is treated as
# "high-conviction enough to spend a research call on". Default chosen to
# match the dashboard's "70+ clean setup" rule of thumb — the scanner
# rarely produces enough 70+ matches per day to make per-symbol research
# expensive.
DEFAULT_CONVICTION_THRESHOLD: float = 70.0


@dataclass(frozen=True)
class ResearchResult:
    """Structured fundamental read on a single ticker.

    Mirrors the mission's checklist. Free-form text per slot is fine —
    the consumer (dashboard expander, report writer) just renders the
    fields that are populated.

    ``confidence`` is in ``[0, 1]`` and represents how much trust the
    researcher places in its own output: 0 = "thin data, don't lean on
    this", 1 = "well-sourced and corroborated". Future ranking code can
    use this to decide whether to surface or suppress the research read.
    """

    ticker: str
    as_of: datetime
    summary: str = ""
    company_quality: str = ""
    management: str = ""
    partnerships: str = ""
    financial_health: str = ""
    key_risks: str = ""
    sources: tuple[str, ...] = ()
    confidence: float = 0.0
    raw: dict[str, object] = field(default_factory=dict)

    def to_row(self) -> dict[str, object]:
        """Flat dict for table / dashboard rendering."""
        return {
            "research_summary": self.summary,
            "research_company_quality": self.company_quality,
            "research_management": self.management,
            "research_partnerships": self.partnerships,
            "research_financial_health": self.financial_health,
            "research_key_risks": self.key_risks,
            "research_sources": ", ".join(self.sources),
            "research_confidence": round(self.confidence, 3),
        }


@runtime_checkable
class Researcher(Protocol):
    """Callable that produces a :class:`ResearchResult` for one ticker.

    Implementations may be slow, network-bound, and expensive. The
    scanner gates calls behind :func:`should_research` and
    :func:`top_candidates` so this stays a cheap, opt-in enhancement —
    never a bottleneck on idea generation.

    Implementations must never raise on data issues; return a
    :class:`ResearchResult` with empty fields and ``confidence=0.0``
    instead. A failing researcher must never abort a scan.
    """

    name: str

    def research(self, ticker: str) -> ResearchResult: ...


class NullResearcher:
    """Baseline researcher that returns an empty result.

    Useful as a default so calling code can unconditionally invoke
    ``researcher.research(sym)`` during the wiring phase without
    branching on whether a real implementation exists.
    """

    name: str = "null"

    def research(self, ticker: str) -> ResearchResult:
        return ResearchResult(
            ticker=ticker.upper(),
            as_of=datetime.now(tz=timezone.utc),
            summary="",
            confidence=0.0,
        )


def should_research(
    score: float, *, threshold: float = DEFAULT_CONVICTION_THRESHOLD
) -> bool:
    """Return True iff ``score`` clears the conviction floor.

    This is the single gate used to decide whether a match is worth a
    research call. Centralizing it here means a future change to the
    policy (e.g. "also research anything with a strong catalyst") lands
    in one place.
    """
    return float(score) >= float(threshold)


def top_candidates(
    matches: Iterable[object],
    *,
    threshold: float = DEFAULT_CONVICTION_THRESHOLD,
    limit: int = 5,
) -> list[object]:
    """Pick the top matches worth spending research on.

    Selection rule: filter by ``effective_score >= threshold``, then
    take the highest ``limit`` by ``effective_score``. Caps the per-run
    cost of research calls even on a wildly bullish day. Accepts any
    object exposing ``effective_score`` (today: ``MatchResult``).
    """
    pool = [m for m in matches if getattr(m, "effective_score", 0.0) >= threshold]
    pool.sort(key=lambda m: getattr(m, "effective_score", 0.0), reverse=True)
    return pool[: max(0, int(limit))]
