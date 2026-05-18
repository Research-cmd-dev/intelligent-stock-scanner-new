"""Catalyst detection for the narrative layer.

A *catalyst* is a discrete event the market reads as a step-change in
the story — a signed contract, a funding round, a capacity expansion,
a strategic pivot. For smaller, emerging names these tend to matter
more than week-over-week sentiment drift: one "signed multi-year
contract with hyperscaler" can re-rate a $200M float overnight.

Detection mirrors :mod:`src.narrative.themes`: each catalyst kind
declares ``phrases`` (any of which triggers the kind) and an optional
``strong_phrases`` set that bumps the strength score. The output is a
:class:`CatalystTag` with ``kind``, a human-readable ``label``, and a
strength in ``[0, 1]``.

To add a catalyst type, drop a :class:`CatalystDef` into
:data:`DEFAULT_CATALYSTS` and add a canned-headline test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class CatalystDef:
    """Definition of one detectable catalyst kind."""

    kind: str
    label: str
    phrases: tuple[str, ...]
    strong_phrases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalystTag:
    """One detected catalyst on one piece of text."""

    kind: str
    label: str
    strength: float
    matched_terms: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------- #
# Catalyst catalog                                                       #
# ---------------------------------------------------------------------- #


DEFAULT_CATALYSTS: tuple[CatalystDef, ...] = (
    CatalystDef(
        kind="contract",
        label="Customer / contract win",
        phrases=(
            "signed contract", "signed agreement", "signed deal",
            "multi-year deal", "multi-year contract", "multiyear contract",
            "customer agreement", "supply agreement", "service agreement",
            "letter of intent", "loi signed", "anchor customer",
            "strategic customer", "tenant signed",
        ),
        strong_phrases=(
            "hyperscaler contract", "hyperscaler agreement",
            "multi-billion contract", "billion-dollar contract",
            "anchor tenant", "long-term offtake",
        ),
    ),
    CatalystDef(
        kind="funding",
        label="Funding / capital raise",
        phrases=(
            "series a", "series b", "series c", "series d", "series e",
            "raised funding", "secured funding", "secured financing",
            "raised $", "equity round", "venture round",
            "credit facility", "term loan", "debt facility",
            "private placement", "secondary offering",
            "convertible note",
        ),
        strong_phrases=(
            "led by", "oversubscribed", "anchor investor",
            "strategic investor", "sovereign wealth", "blackstone",
            "blackrock", "kkr", "sequoia",
        ),
    ),
    CatalystDef(
        kind="expansion",
        label="Capacity / facility expansion",
        phrases=(
            "capacity expansion", "expand capacity", "expanding capacity",
            "new facility", "new site", "groundbreaking",
            "new data center", "additional capacity",
            "double capacity", "triple capacity", "expansion plan",
            "expansion announcement", "additional megawatts",
            "additional gigawatt", "fleet expansion",
        ),
        strong_phrases=(
            "doubles capacity", "triples capacity", "ten times capacity",
            "1 gw", "2 gw", "5 gw", "gigawatt-scale",
            "first phase complete", "phase two", "phase three",
        ),
    ),
    CatalystDef(
        kind="pivot",
        label="Strategic pivot / repositioning",
        phrases=(
            "pivot to ai", "pivot to hpc", "transition to ai",
            "shift to ai", "shift to hpc", "repurpose",
            "repurposing", "rebranded", "rebranding",
            "strategic shift", "new strategic direction",
            "exit bitcoin", "exiting bitcoin mining",
        ),
        strong_phrases=(
            "fully pivoting", "pivoting away from", "complete pivot",
            "pivots to ai", "ditch bitcoin", "abandon bitcoin",
        ),
    ),
    CatalystDef(
        kind="m_and_a",
        label="M&A activity",
        phrases=(
            "agrees to acquire", "agreed to acquire", "acquires",
            "acquisition of", "to acquire", "merger with",
            "merger agreement", "takeover offer", "tender offer",
            "all-cash deal", "cash and stock deal", "going private",
        ),
        strong_phrases=(
            "strategic acquisition", "transformational acquisition",
            "premium of",
        ),
    ),
    CatalystDef(
        kind="earnings_beat",
        label="Earnings beat / guidance raise",
        phrases=(
            "beat estimates", "beats estimates", "beat expectations",
            "raised guidance", "raises guidance", "raised outlook",
            "exceeded expectations", "ahead of consensus",
            "guidance increased", "upgraded outlook",
        ),
        strong_phrases=(
            "smashed estimates", "blew past estimates",
            "record quarter", "record revenue",
        ),
    ),
    CatalystDef(
        kind="partnership",
        label="Strategic partnership",
        phrases=(
            "strategic partnership", "partnership with",
            "joint venture", "strategic alliance",
            "exclusive partnership", "preferred partner",
            "collaboration with",
        ),
        strong_phrases=(
            "multi-year partnership", "global partnership",
            "exclusive supplier",
        ),
    ),
    CatalystDef(
        kind="approval",
        label="Regulatory approval / clearance",
        phrases=(
            "fda approval", "fda approved", "fda clearance",
            "ce mark", "regulatory approval", "license granted",
            "license approved", "permits granted", "permit approval",
            "interconnect approval", "ferc approval",
        ),
        strong_phrases=(
            "full approval", "expedited approval",
            "breakthrough designation",
        ),
    ),
)


# ---------------------------------------------------------------------- #
# Detection                                                              #
# ---------------------------------------------------------------------- #


def detect_catalysts(
    text: str, catalysts: Iterable[CatalystDef] = DEFAULT_CATALYSTS,
    *, min_strength: float = 0.3,
) -> list[CatalystTag]:
    """Tag ``text`` with every catalyst kind that matches.

    Strength starts at ``0.4`` for a single phrase hit and grows with
    each additional regular phrase (+0.1) and each "strong" phrase
    (+0.2). One tag per kind even if multiple phrases of that kind hit
    — the explanation builder only wants "contract" once, not three
    times.
    """
    if not text:
        return []
    haystack = text.lower()

    out: list[CatalystTag] = []
    for cat in catalysts:
        tag = _evaluate_catalyst(haystack, cat)
        if tag is not None and tag.strength >= min_strength:
            out.append(tag)

    out.sort(key=lambda c: c.strength, reverse=True)
    return out


def _evaluate_catalyst(text: str, cat: CatalystDef) -> CatalystTag | None:
    hits = [p for p in cat.phrases if p in text]
    strong_hits = [p for p in cat.strong_phrases if p in text]
    if not hits and not strong_hits:
        return None

    strength = 0.4
    strength += 0.10 * max(0, len(hits) - 1)
    strength += 0.20 * len(strong_hits)
    strength = max(0.0, min(1.0, strength))

    matched = tuple(dict.fromkeys(hits + strong_hits))
    return CatalystTag(
        kind=cat.kind, label=cat.label, strength=strength,
        matched_terms=matched,
    )


# ---------------------------------------------------------------------- #
# Aggregation across items                                               #
# ---------------------------------------------------------------------- #


def aggregate_catalysts(
    item_tags: list[list[CatalystTag]], *, top_k: int = 3,
) -> tuple[CatalystTag, ...]:
    """Roll item-level catalyst tags up to a narrative-level summary.

    Repeated mentions reinforce: the same catalyst showing up across
    multiple articles increases its aggregate strength (independent
    confirmations matter). We sum 0.6x the per-item strength so two
    independent mentions hit ~0.7-0.8 even from moderate per-item hits.
    """
    agg: dict[str, dict[str, object]] = {}
    for tags in item_tags:
        for c in tags:
            slot = agg.setdefault(c.kind, {
                "label": c.label, "strength": 0.0, "matched": set(),
            })
            slot["strength"] = min(1.0, float(slot["strength"]) + c.strength * 0.6)
            slot["matched"].update(c.matched_terms)  # type: ignore[union-attr]

    out = [
        CatalystTag(
            kind=kind,
            label=str(slot["label"]),
            strength=float(slot["strength"]),
            matched_terms=tuple(sorted(slot["matched"])),  # type: ignore[arg-type]
        )
        for kind, slot in agg.items()
    ]
    out.sort(key=lambda c: c.strength, reverse=True)
    return tuple(out[:top_k])
