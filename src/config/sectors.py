"""
Curated Thematic Universe for the Stock Finder Agent

This module defines the core set of US-listed tickers used across the entire
project, including:

- Scanner & pattern detection
- Narrative layer (themes + catalysts)
- Backtesting and feature evaluation
- Modal remote compute jobs (downloads + backtests)

Each sector contains a thoughtfully curated list of 30–50 high-quality, liquid
companies with strong relevance to the macro theme. The lists prioritize
established businesses with real fundamentals and aim for a healthy balance
between large-cap leaders and high-quality mid-cap names.

Editing Guidelines
------------------
- Quality > Quantity
- Stocks must have genuine, strong relevance to the sector/theme
- Prefer liquid, established companies over speculative microcaps
- Minimize unnecessary duplication across sectors when possible
- When adding new sectors or significantly expanding existing ones,
  update this docstring and the progress_summary.txt

Last Updated: 2026-05-20
"""

from __future__ import annotations

from typing import Iterable

__all__ = [
    "SECTOR_TICKERS",
    "SECTORS",
    "all_tickers",
    "tickers_for_sectors",
    "sector_for",
]

# Primary data structure: Sector → list of high-quality, liquid US-listed tickers.
SECTOR_TICKERS: dict[str, list[str]] = {
    "AI": [
        "NVDA", "AVGO", "AMD", "TSM", "ARM", "ASML", "AMAT", "LRCX", "KLAC", "MU",
        "INTC", "QCOM", "MRVL", "MPWR", "ON", "ADI", "TXN", "NXPI", "SWKS", "MCHP",
        "CRWD", "PANW", "FTNT", "ZS", "SNOW", "DDOG", "NET", "MDB", "ESTC", "SPLK",
        "ANET", "CIEN", "JNPR", "FFIV", "NTAP", "WDC", "STX", "PSTG", "SMCI", "VRT",
        "ETN", "GEV"
    ],
    "Chips": [
        "NVDA", "AVGO", "AMD", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "MU", "INTC",
        "QCOM", "MRVL", "MPWR", "ON", "ADI", "TXN", "NXPI", "SWKS", "MCHP", "TER",
        "COHR", "AEHR", "ACLS", "FORM", "UCTT", "ENTG", "AMKR", "SIMO", "SLAB", "CRUS",
        "DIOD", "MTSI", "POWI", "SYNA", "SMTC", "MP", "LSCC", "QRVO"
    ],
    "Nuclear": [
        "VST", "CEG", "TLN", "OKLO", "BWXT", "SMR", "LEU", "UEC", "UUUU", "CCJ",
        "NXE", "DNN", "KAP", "EFR", "FCUUF", "URG", "LTBR", "NUKZ", "NLR", "HALE",
        "CWCO", "FLR", "PWR", "EME", "FIX", "APG", "ACM", "J", "KBR", "TTEK",
        "WSC", "VMI", "ETN", "GEV", "HUBB", "ENS", "ENSG", "EXC", "XEL", "AEP"
    ],
    "Space": [
        "RKLB", "ASTS", "LUNR", "RDW", "SPCE", "KTOS", "NOC", "LMT", "BA", "RTX",
        "GD", "TDY", "HON", "LHX", "HII", "MAXR", "PL", "SRAC", "SPIR", "SIDU",
        "LLAP", "ASTR", "MNTS", "KSCP", "SATL", "IONQ", "QBTS", "RGTI", "QTUM",
        "UFO", "ARKX", "ARKQ", "ARKF", "ARKG"
    ],
    "Robotics": [
        "ISRG", "ABB", "IRBT", "TER", "PATH", "SYM", "BDTX", "FARO", "NOVT", "JBT",
        "GTES", "EMR", "ROK", "AME", "ITW", "DOV", "NDSN", "MIDD", "GTLS", "FLOW",
        "ST", "RXN", "ATS", "CW", "KAI", "AIMC", "HLIO", "PFIN", "IEX", "ITT",
        "RBC", "CR", "GGG", "LECO", "MWA", "WTS", "FELE", "CIR"
    ],
    "Bio": [
        "AMGN", "GILD", "VRTX", "REGN", "BIIB", "MRNA", "BNTX", "CRSP", "NTLA", "EDIT",
        "BEAM", "VERV", "RCKT", "FOLD", "ALNY", "SRPT", "EXEL", "INCY", "BLUE", "SAGE",
        "PTCT", "NBIX", "UTHR", "VTRS", "BMY", "ABBV", "LLY", "MRK", "PFE", "JNJ",
        "AZN", "NVO", "SNY", "GSK", "TAK", "RHHBY", "BAYRY", "NOVO", "ILMN",
        "PACB", "TWST", "TXG", "CRSP"
    ],
}

# Backward compatibility alias
SECTORS = SECTOR_TICKERS


def all_tickers() -> list[str]:
    """Deduplicated, sorted list of every ticker in the universe."""
    seen: set[str] = set()
    for symbols in SECTOR_TICKERS.values():
        seen.update(symbols)
    return sorted(seen)


def tickers_for_sectors(sectors: Iterable[str]) -> list[str]:
    """Deduplicated tickers for the requested subset of sectors."""
    out: set[str] = set()
    for name in sectors:
        out.update(SECTOR_TICKERS.get(name, []))
    return sorted(out)


def sector_for(ticker: str) -> list[str]:
    """Sectors a ticker belongs to (a ticker can appear in multiple themes)."""
    return [name for name, syms in SECTOR_TICKERS.items() if ticker in syms]
