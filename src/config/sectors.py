"""Curated thematic universe for the Stock Finder Agent.

Edit this file to retune sector coverage. Tickers are intentionally large/liquid
names plus a small number of high-conviction smaller plays per theme.
"""

from __future__ import annotations

from typing import Iterable

# Sector → list of US-listed tickers.
SECTORS: dict[str, list[str]] = {
    "AI": [
        "NVDA", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "AI", "SNOW",
        "CRWD", "NOW", "ORCL", "AMD", "ANET", "TSM",
    ],
    "Chips": [
        "NVDA", "AMD", "TSM", "AVGO", "ASML", "MU", "QCOM", "INTC",
        "ARM", "LRCX", "AMAT", "MRVL", "ON", "SMCI",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "OXY", "SLB", "EOG", "PSX",
        # Nuclear / uranium subtheme
        "CCJ", "URA", "URNM", "OKLO", "SMR", "LEU", "BWXT",
    ],
    "Bio": [
        "LLY", "NVO", "MRK", "VRTX", "REGN", "ISRG", "AMGN",
        "CRSP", "BEAM", "NTLA", "MRNA", "RXRX",
    ],
    "Space": [
        "RKLB", "LMT", "BA", "ASTS", "IRDM", "PL", "LUNR", "SPIR", "RTX",
    ],
    "Batteries": [
        "TSLA", "ALB", "LAC", "QS", "PLUG", "ENPH", "FSLR", "STLA", "RIVN",
    ],
    "Quantum": [
        "IBM", "GOOGL", "IONQ", "RGTI", "QBTS", "QUBT",
    ],
    "Defense": [
        "LMT", "RTX", "NOC", "GD", "LHX", "HII", "KTOS", "AVAV", "PLTR",
    ],
    "Robotics": [
        "ABB", "ISRG", "ROK", "TER", "IRBT", "SYM", "PATH", "FANUY",
    ],
}


def all_tickers() -> list[str]:
    """Deduplicated, sorted list of every ticker in the universe."""
    seen: set[str] = set()
    for symbols in SECTORS.values():
        seen.update(symbols)
    return sorted(seen)


def tickers_for_sectors(sectors: Iterable[str]) -> list[str]:
    """Deduplicated tickers for the requested subset of sectors."""
    out: set[str] = set()
    for name in sectors:
        out.update(SECTORS.get(name, []))
    return sorted(out)


def sector_for(ticker: str) -> list[str]:
    """Sectors a ticker belongs to (a ticker can appear in multiple themes)."""
    return [name for name, syms in SECTORS.items() if ticker in syms]
