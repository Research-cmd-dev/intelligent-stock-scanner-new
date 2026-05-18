"""Pattern detectors.

Each detector is a pure function ``(df, symbol) -> MatchResult | None``.
Adding a new pattern is a one-file change: write the detector here, then
register it in :data:`ALL_DETECTORS` so :class:`src.scanner.scanner.Scanner`
picks it up automatically.
"""

from __future__ import annotations

from .base import Detector, Factor, MatchResult
from .bottom_hunter import NAME as BOTTOM_HUNTER, detect_bottom_hunter
from .trend_rider import NAME as TREND_RIDER, detect_trend_rider

# Public registry. Order is preserved when multiple patterns hit the same
# symbol — the orchestrator emits one MatchResult per (symbol, pattern).
ALL_DETECTORS: tuple[tuple[str, Detector], ...] = (
    (TREND_RIDER, detect_trend_rider),
    (BOTTOM_HUNTER, detect_bottom_hunter),
)

__all__ = [
    "ALL_DETECTORS",
    "BOTTOM_HUNTER",
    "Detector",
    "Factor",
    "MatchResult",
    "TREND_RIDER",
    "detect_bottom_hunter",
    "detect_trend_rider",
]
