"""Shared types for pattern detectors.

A *detector* is a callable that accepts an indicator-augmented OHLCV frame
plus a symbol, and returns either a :class:`MatchResult` describing the hit
or ``None`` if the pattern is not present. Detectors must be pure functions
of the input frame — no global state, no network I/O.

Scores are always on a 0-100 scale where higher is a stronger signal.
Detectors document their own component weights, but consumers can treat the
final ``score`` as comparable across patterns: 70+ is a clean setup, 50-70 is
worth a look, below 50 should rarely surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class Factor:
    """One scored component contributing to a :class:`MatchResult`.

    ``value`` is the raw metric (e.g. an RSI reading). ``contribution`` is
    the points this factor added to the 0-100 score. ``weight`` is the
    factor's maximum possible contribution, so a narrative layer can render
    "RSI 38 — 18/20 pts" without needing to know detector internals.
    """

    name: str
    value: float
    contribution: float
    weight: float
    note: str = ""


@dataclass(frozen=True)
class MatchResult:
    """A single pattern hit for one symbol on one bar.

    Frozen on purpose: results flow from scanner → ranking → narrative →
    dashboard, and downstream layers should never mutate them.
    """

    symbol: str
    pattern: str
    score: float                       # 0-100, higher is stronger
    as_of: pd.Timestamp                # bar date the detector evaluated
    price: float                       # close on ``as_of``
    sectors: tuple[str, ...] = ()      # populated by orchestrator
    themes: tuple[str, ...] = ()       # populated by orchestrator
    source: str = "unknown"            # "polygon" | "yfinance" | "unknown"
    indicators: dict[str, float] = field(default_factory=dict)
    factors: tuple[Factor, ...] = ()

    def to_row(self) -> dict[str, object]:
        """Flat row suitable for a DataFrame / table view."""
        return {
            "symbol": self.symbol,
            "pattern": self.pattern,
            "score": round(self.score, 1),
            "as_of": self.as_of,
            "price": round(self.price, 2),
            "sectors": ", ".join(self.sectors),
            "themes": ", ".join(self.themes),
            "rsi14": self.indicators.get("rsi14"),
            "dist_sma50_pct": _pct(self.indicators.get("dist_sma50")),
            "dist_sma200_pct": _pct(self.indicators.get("dist_sma200")),
            "drawdown_120_pct": _pct(self.indicators.get("drawdown_120")),
            "source": self.source,
        }


class Detector(Protocol):
    """Callable signature every pattern detector implements."""

    name: str

    def __call__(
        self, df: pd.DataFrame, symbol: str
    ) -> MatchResult | None: ...


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Constrain ``x`` to ``[lo, hi]``. Used everywhere in scoring."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def triangular(x: float, lo: float, peak: float, hi: float) -> float:
    """Triangular membership function in [0, 1].

    Returns 0 outside ``[lo, hi]``, peaks at 1.0 when ``x == peak``, and
    interpolates linearly on each side. Useful for "best when in a band"
    scoring — e.g. RSI is ideal at 38, acceptable from 30 to 45.
    """
    if x <= lo or x >= hi:
        return 0.0
    if x == peak:
        return 1.0
    if x < peak:
        return (x - lo) / (peak - lo)
    return (hi - x) / (hi - peak)


def _pct(x: float | None) -> float | None:
    if x is None:
        return None
    return round(x * 100, 2)
