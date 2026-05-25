"""Theme novelty detector.

Given a list of today's themes and the last N days of briefings, returns
the subset of themes that did NOT appear in any prior briefing's
``themes_today`` list. Comparison is case-insensitive and ignores
surrounding whitespace; substring matching is intentionally NOT done
(``"AI capex"`` and ``"AI capex saturation"`` are treated as distinct).

Pure function. No LLM, no I/O.
"""

from __future__ import annotations

import json
from typing import Any


def emerging_themes(
    todays_themes: list[str],
    recent_briefings: list[dict[str, Any]],
) -> list[str]:
    """Themes from ``todays_themes`` that don't appear in any ``recent_briefings``.

    ``recent_briefings`` are rows from ``TranscriptStore.list_recent_briefings``;
    each must have a ``structured_json`` field whose ``aggregation.themes_today``
    list we compare against.
    """
    seen = _collect_prior_themes(recent_briefings)
    out: list[str] = []
    seen_lower = {t.casefold() for t in seen}
    for t in todays_themes:
        key = t.strip().casefold()
        if key and key not in seen_lower:
            out.append(t)
    return out


def _collect_prior_themes(recent_briefings: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in recent_briefings:
        raw = row.get("structured_json")
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        agg = (data or {}).get("aggregation") or {}
        for t in agg.get("themes_today") or []:
            if isinstance(t, str):
                out.add(t.strip())
    return out
