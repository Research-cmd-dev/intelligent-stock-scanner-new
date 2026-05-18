"""Theme detection for the narrative layer.

A *theme* is a recurring storyline the agent wants to recognize and
score above generic coverage — "AI infrastructure," "miner-to-AI
pivot," "cheap power + GPU compute," etc. Detection is keyword-based
and intentionally simple so the rules are auditable and easy to retune
without retraining anything.

Each :class:`ThemeDef` declares:

- ``core_terms`` — at least one must appear for the theme to fire.
- ``support_terms`` — supporting evidence; each match increases
  the relevance score but isn't required.
- ``required_pairs`` — for *composite* themes (e.g. "cheap power"
  *and* "GPU compute"). Both elements of at least one pair must
  appear together, even when ``core_terms`` are listed.
- ``aliases`` — additional names the theme is searched as.

Returned :class:`ThemeTag` records are intentionally small — name,
relevance ∈ [0, 1], and the terms that fired. The scorer aggregates
these across items; the explanation builder mentions the strongest
themes in plain English.

To add or retune a theme, edit :data:`DEFAULT_THEMES` and add a
canned-headline test in ``tests/test_themes_catalysts.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ThemeDef:
    """Definition of one detectable narrative theme."""

    name: str
    core_terms: tuple[str, ...]
    support_terms: tuple[str, ...] = ()
    required_pairs: tuple[tuple[str, str], ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThemeTag:
    """One detected theme on one piece of text.

    ``relevance`` is in ``[0, 1]``; a value above ``0.5`` means
    multiple supporting terms also fired, not just a single keyword.
    """

    name: str
    relevance: float
    matched_terms: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------- #
# Theme catalog                                                          #
# ---------------------------------------------------------------------- #
#
# Order matters slightly: composite themes (e.g. "Power + Compute") are
# defined before the parent themes (AI Infrastructure, Cheap Power) so
# that when the explanation builder picks the strongest theme by
# relevance, the more-specific composite shows up first on ties.


DEFAULT_THEMES: tuple[ThemeDef, ...] = (
    ThemeDef(
        name="Miner-to-AI Pivot",
        core_terms=(
            "bitcoin miner", "bitcoin mining", "btc miner", "crypto miner",
            "hash rate", "hashrate", "mining facility", "mining rig",
        ),
        support_terms=(
            "pivot to ai", "pivot to hpc", "repurpose", "ai workload",
            "high-performance computing", "hpc", "ai compute", "gpu compute",
            "ai data center", "ai hosting", "neocloud",
        ),
        required_pairs=(
            ("bitcoin miner", "ai"), ("bitcoin mining", "ai"),
            ("btc miner", "ai"), ("crypto miner", "ai"),
            ("mining facility", "ai"), ("mining facility", "hpc"),
            ("hash rate", "ai"), ("hashrate", "ai"),
            ("bitcoin miner", "hpc"), ("bitcoin mining", "hpc"),
        ),
    ),
    ThemeDef(
        name="Power + Compute",
        core_terms=(
            "power purchase agreement", "ppa", "behind the meter",
            "low-cost power", "cheap power", "hydropower", "hydroelectric",
            "geothermal", "stranded power", "stranded energy",
            "power costs", "energy costs",
        ),
        support_terms=(
            "gpu", "ai compute", "data center", "training cluster",
            "inference compute", "hyperscaler", "iceland", "norway",
            "paraguay", "quebec", "manitoba", "canada hydro",
        ),
        required_pairs=(
            ("ppa", "gpu"), ("ppa", "ai"), ("hydropower", "ai"),
            ("hydropower", "gpu"), ("geothermal", "ai"),
            ("cheap power", "ai"), ("cheap power", "gpu"),
            ("behind the meter", "ai"), ("behind the meter", "gpu"),
            ("stranded power", "ai"), ("stranded energy", "ai"),
            ("hydroelectric", "ai"), ("hydroelectric", "gpu"),
        ),
    ),
    ThemeDef(
        name="Neocloud / GPU Cloud",
        core_terms=(
            "neocloud", "gpu cloud", "ai cloud", "gpu-as-a-service",
            "gpu as a service", "coreweave", "lambda labs", "crusoe",
            "gpu rental", "gpu hosting", "ai inference platform",
        ),
        support_terms=(
            "h100", "h200", "b200", "blackwell", "hopper",
            "nvidia gpu", "training capacity", "inference capacity",
            "compute marketplace", "compute capacity",
        ),
    ),
    ThemeDef(
        name="Data Center Build-out",
        core_terms=(
            "data center", "data-center", "datacenter", "hyperscale",
            "colocation", "colo facility", "campus expansion",
        ),
        support_terms=(
            "megawatt", "megawatts", "mw", "gigawatt", "gigawatts", "gw",
            "capacity expansion", "new facility", "groundbreaking",
            "construction", "site selection", "build-out", "buildout",
        ),
        required_pairs=(
            ("data center", "megawatt"), ("data center", "gigawatt"),
            ("data center", "expansion"), ("data center", "construction"),
            ("data center", "buildout"), ("data center", "build-out"),
            ("hyperscale", "megawatt"), ("hyperscale", "gigawatt"),
        ),
    ),
    ThemeDef(
        name="Nuclear for AI",
        core_terms=(
            "small modular reactor", "smr", "modular nuclear",
            "nuclear power", "nuclear reactor", "advanced reactor",
            "microreactor",
        ),
        support_terms=(
            "ai", "data center", "training", "compute", "hyperscaler",
            "behind the meter", "ppa",
        ),
        required_pairs=(
            ("smr", "data center"), ("smr", "ai"), ("smr", "hyperscaler"),
            ("nuclear", "data center"), ("nuclear", "ai compute"),
            ("modular reactor", "ai"), ("microreactor", "data center"),
        ),
    ),
    ThemeDef(
        name="AI Infrastructure",
        core_terms=(
            "ai infrastructure", "ai infra", "training cluster",
            "inference cluster", "ai supercomputer",
            "gpu cluster", "gpu fleet", "ai factory",
            "frontier model", "foundation model",
        ),
        support_terms=(
            "hyperscaler", "nvidia", "amd mi300", "h100", "h200", "b200",
            "training capacity", "inference compute", "data center",
            "megawatt", "gigawatt",
        ),
    ),
    ThemeDef(
        name="Sovereign AI",
        core_terms=(
            "sovereign ai", "national ai", "sovereign compute",
            "national compute", "ai sovereignty",
        ),
        support_terms=(
            "government", "ministry", "uae", "saudi", "india",
            "japan", "uk ai", "france ai", "data residency",
            "export controls",
        ),
    ),
)


# ---------------------------------------------------------------------- #
# Detection                                                              #
# ---------------------------------------------------------------------- #


def detect_themes(
    text: str, themes: Iterable[ThemeDef] = DEFAULT_THEMES,
    *, min_relevance: float = 0.3,
) -> list[ThemeTag]:
    """Tag ``text`` with every theme whose rules match.

    Returns the tags sorted by relevance descending so callers can take
    the top-K without re-sorting.
    """
    if not text:
        return []
    haystack = text.lower()

    out: list[ThemeTag] = []
    for theme in themes:
        tag = _evaluate_theme(haystack, theme)
        if tag is not None and tag.relevance >= min_relevance:
            out.append(tag)

    out.sort(key=lambda t: t.relevance, reverse=True)
    return out


def _evaluate_theme(text: str, theme: ThemeDef) -> ThemeTag | None:
    """Score a single theme against ``text``.

    Returns ``None`` when the theme doesn't fire. Relevance grows with
    core matches first, then supporting matches; required pairs (if
    declared) gate the whole match.
    """
    core_hits = [t for t in theme.core_terms if t in text]
    alias_hits = [t for t in theme.aliases if t in text]
    support_hits = [t for t in theme.support_terms if t in text]

    if not core_hits and not alias_hits:
        return None

    # Composite themes: require co-occurrence of at least one declared pair.
    if theme.required_pairs:
        pair_satisfied = any(a in text and b in text for a, b in theme.required_pairs)
        if not pair_satisfied:
            return None

    # Relevance: a single core term lands you at 0.35; each additional
    # core match adds 0.15; each support match adds 0.10. Capped at 1.0.
    relevance = 0.35
    relevance += 0.15 * (len(core_hits) + len(alias_hits) - 1)
    relevance += 0.10 * len(support_hits)
    relevance = max(0.0, min(1.0, relevance))

    matched = tuple(dict.fromkeys(core_hits + alias_hits + support_hits))
    return ThemeTag(name=theme.name, relevance=relevance, matched_terms=matched)


# ---------------------------------------------------------------------- #
# Aggregation across items                                               #
# ---------------------------------------------------------------------- #


def aggregate_themes(
    item_tags: list[list[ThemeTag]], *, top_k: int = 3,
) -> tuple[ThemeTag, ...]:
    """Roll item-level theme tags up to a narrative-level summary.

    Items reinforce each other: a theme that fires on three articles
    gets a stronger aggregate than one that fires on a single article,
    even at higher per-item relevance. We sum relevance scores per
    theme name and cap at 1.0.
    """
    agg: dict[str, dict[str, object]] = {}
    for tags in item_tags:
        for t in tags:
            slot = agg.setdefault(
                t.name, {"relevance": 0.0, "matched": set()}
            )
            slot["relevance"] = min(1.0, float(slot["relevance"]) + t.relevance * 0.5)
            slot["matched"].update(t.matched_terms)  # type: ignore[union-attr]

    out = [
        ThemeTag(
            name=name,
            relevance=float(slot["relevance"]),
            matched_terms=tuple(sorted(slot["matched"])),  # type: ignore[arg-type]
        )
        for name, slot in agg.items()
    ]
    out.sort(key=lambda t: t.relevance, reverse=True)
    return tuple(out[:top_k])
