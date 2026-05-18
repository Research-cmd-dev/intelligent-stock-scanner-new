"""Scanner package public API.

Importing this package is cheap — no network I/O, no settings reads. The
heavy work happens when you instantiate :class:`Scanner` or call
:func:`run_scan`.
"""

from __future__ import annotations

from .indicators import add_indicators, snapshot
from .patterns import (
    ALL_DETECTORS, BOTTOM_HUNTER, TREND_RIDER,
    Factor, MatchResult,
    detect_bottom_hunter, detect_trend_rider,
)
from .scanner import ScanReport, Scanner, run_scan
from .universe import build_universe, classify

__all__ = [
    "ALL_DETECTORS",
    "BOTTOM_HUNTER",
    "Factor",
    "MatchResult",
    "ScanReport",
    "Scanner",
    "TREND_RIDER",
    "add_indicators",
    "build_universe",
    "classify",
    "detect_bottom_hunter",
    "detect_trend_rider",
    "run_scan",
    "snapshot",
]
