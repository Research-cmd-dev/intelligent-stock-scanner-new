"""Discovery universe construction.

The thematic single-stock universe lives in ``src.config.sectors``. This module
adds two complementary layers and offers a single :func:`build_universe`
helper that detectors and the orchestrator both consume:

* **Sector / thematic ETFs** — proxies for an entire theme. Useful because an
  ETF often gives the cleanest read on a sector setup before any single name
  in it confirms.
* **Broad market** — SPY / QQQ / IWM / DIA. Always good context; many setups
  only matter if the broad tape is cooperating.

Categories are exposed as plain dicts/lists so callers (and tests) can pick
the exact slice they want without needing to know the internal layout.
"""

from __future__ import annotations

from src.config.sectors import SECTORS, all_tickers, tickers_for_sectors

# SPDR sector ETFs — the canonical "what's working" sector map.
SPDR_SECTOR_ETFS: list[str] = [
    "XLK",   # Technology
    "XLC",   # Communication Services
    "XLY",   # Consumer Discretionary
    "XLP",   # Consumer Staples
    "XLE",   # Energy
    "XLF",   # Financials
    "XLV",   # Health Care
    "XLI",   # Industrials
    "XLB",   # Materials
    "XLRE",  # Real Estate
    "XLU",   # Utilities
]

# Theme ETFs aligned with our high-conviction sectors. One or two per theme;
# we want representative exposure, not exhaustive overlap.
THEME_ETFS: dict[str, list[str]] = {
    "AI":        ["BOTZ", "ARKQ"],
    "Chips":     ["SMH", "SOXX"],
    "Energy":    ["XLE", "XOP"],
    "Nuclear":   ["URA", "URNM", "NLR"],
    "Bio":       ["XBI", "IBB"],
    "Space":     ["ARKX", "UFO"],
    "Batteries": ["LIT", "BATT"],
    "Defense":   ["ITA", "XAR"],
    "Robotics":  ["BOTZ", "ROBO"],
    "Clean":     ["ICLN", "TAN"],
}

# Broad-market index ETFs.
BROAD_MARKET: list[str] = ["SPY", "QQQ", "IWM", "DIA", "MDY"]


def all_theme_etfs() -> list[str]:
    """Deduplicated, sorted list of every theme ETF."""
    seen: set[str] = set()
    for etfs in THEME_ETFS.values():
        seen.update(etfs)
    return sorted(seen)


def build_universe(
    *,
    sectors: list[str] | None = None,
    include_sector_etfs: bool = True,
    include_theme_etfs: bool = True,
    include_broad_market: bool = False,
    extra: list[str] | None = None,
) -> list[str]:
    """Compose a deduplicated, sorted discovery universe.

    Args:
        sectors: Restrict the single-stock pool to these sector names
            (see :data:`src.config.sectors.SECTORS`). ``None`` means all
            sectors. Pass ``[]`` to exclude single stocks entirely.
        include_sector_etfs: Add SPDR sector ETFs.
        include_theme_etfs: Add the thematic ETFs (SMH, URA, XBI, …).
        include_broad_market: Add SPY / QQQ / IWM / DIA / MDY.
        extra: Free-form ticker additions (e.g. a user's watchlist).
    """
    out: set[str] = set()

    if sectors is None:
        out.update(all_tickers())
    elif sectors:
        out.update(tickers_for_sectors(sectors))

    if include_sector_etfs:
        out.update(SPDR_SECTOR_ETFS)
    if include_theme_etfs:
        out.update(all_theme_etfs())
    if include_broad_market:
        out.update(BROAD_MARKET)
    if extra:
        out.update(s.upper() for s in extra)

    return sorted(out)


def classify(ticker: str) -> dict[str, list[str]]:
    """Best-effort classification of ``ticker`` across our taxonomies.

    Returns a dict with keys ``sectors`` and ``themes`` listing every
    bucket the ticker appears in. Unknown tickers return empty lists.
    """
    sectors = [name for name, syms in SECTORS.items() if ticker in syms]
    themes = [name for name, etfs in THEME_ETFS.items() if ticker in etfs]
    return {"sectors": sectors, "themes": themes}
