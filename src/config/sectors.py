"""
Curated Thematic Universe for the Stock Finder Agent

This module defines the core set of US-listed tickers used across the entire
project, including:

- Scanner & pattern detection
- Narrative layer (themes + catalysts)
- Backtesting and feature evaluation
- Modal remote compute jobs (downloads + backtests)

Each sector contains a thoughtfully curated list of 35–55 high-quality, liquid
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

Last Updated: 2026-05-21 (pruned 25 non-viable tickers with no market data)
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
        "CRWD", "PANW", "FTNT", "ZS", "SNOW", "DDOG", "NET", "MDB", "ESTC",
        "ANET", "CIEN", "FFIV", "NTAP", "WDC", "STX", "PSTG", "SMCI", "VRT",
        "ETN", "GEV", "AMZN", "MSFT", "GOOGL", "ORCL", "IBM", "CSCO", "CDNS", "SNPS"
    ],
    "Chips": [
        "NVDA", "AVGO", "AMD", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "MU", "INTC",
        "QCOM", "MRVL", "MPWR", "ON", "ADI", "TXN", "NXPI", "SWKS", "MCHP", "TER",
        "COHR", "AEHR", "ACLS", "FORM", "UCTT", "ENTG", "AMKR", "SIMO", "SLAB", "CRUS",
        "DIOD", "MTSI", "POWI", "SYNA", "SMTC", "MP", "LSCC", "QRVO"
    ],
    "Energy": [
        "VST", "CEG", "TLN", "OKLO", "BWXT", "SMR", "LEU", "UEC", "UUUU", "CCJ",
        "NXE", "DNN", "URG", "LTBR",
        "FLR", "PWR", "EME", "FIX", "APG", "ACM", "J", "KBR", "TTEK",
        "ETN", "GEV", "HUBB", "ENS", "EXC", "XEL", "AEP",
        "DUK", "NEE", "SO", "D", "ED", "FE", "PEG", "ETR", "CNP", "AES", "BEPC", "BEP"
    ],
    # New/expanded for doc consistency (README/PROJECT). Quality-focused lists;
    # overlap with existing sectors is intentional and deduped by all_tickers().
    "Batteries": [
        "ENPH", "SEDG", "FSLR", "RUN", "STEM", "FLNC", "BE", "PLUG", "BLDP",
        "FCEL", "CWEN", "AY", "ORA", "HASI", "NXT", "AMSC", "POWL", "ENS",
        "SPWR", "JKS", "CSIQ", "ARRY", "SHLS"
    ],
    "Quantum": [
        "IONQ", "QBTS", "RGTI", "QUBT", "ARQQ", "QMCO", "PXLW", "CRDO", "ATOM"
    ],
    "Defense": [
        "LMT", "RTX", "NOC", "GD", "BA", "LHX", "HII", "TDG", "HEI", "KTOS",
        "AVAV", "CW", "DRS", "ESLT", "HXL", "BWXT", "TXT", "SPR", "MOG.A",
        "TDY", "ITA", "XAR"
    ],
    "Nuclear": [
        "CCJ", "UEC", "UUUU", "NXE", "DNN", "BWXT", "LEU", "OKLO", "SMR",
        "NLR", "CEG", "VST", "TLN"
    ],
    "Space": [
        "RKLB", "ASTS", "LUNR", "RDW", "SPCE", "KTOS", "NOC", "LMT", "BA", "RTX",
        "GD", "TDY", "HON", "LHX", "HII", "PL", "SPIR", "SIDU",
        "MNTS", "KSCP", "SATL", "IONQ", "QBTS", "RGTI", "QTUM", "MDA"
    ],
    "Robotics": [
        "ISRG", "TER", "PATH", "SYM", "BDTX", "NOVT",
        "GTES", "EMR", "ROK", "AME", "ITW", "DOV", "NDSN", "MIDD", "GTLS", "FLOW",
        "ST", "ATS", "CW", "KAI", "HLIO", "IEX", "ITT",
        "RBC", "CR", "GGG", "LECO", "MWA", "WTS", "FELE", "ROCK"
    ],
    "Bio": [
        "AMGN", "GILD", "VRTX", "REGN", "BIIB", "MRNA", "BNTX", "CRSP", "NTLA", "EDIT",
        "BEAM", "RCKT", "FOLD", "ALNY", "SRPT", "EXEL", "INCY",
        "PTCT", "NBIX", "UTHR", "VTRS", "BMY", "ABBV", "LLY", "MRK", "PFE", "JNJ",
        "AZN", "NVO", "SNY", "GSK", "TAK", "RHHBY", "BAYRY", "ILMN",
        "PACB", "TWST", "TXG"
    ],
    "Software": [
        "MSFT", "GOOGL", "AMZN", "CRM", "ADBE", "ORCL", "SAP", "SNOW", "DDOG", "NET",
        "MDB", "ESTC", "ANET", "PANW", "CRWD", "FTNT", "ZS", "PLTR", "PATH",
        "SYM", "UI", "UPST", "AI", "HUBS", "WDAY", "NOW", "TEAM", "DOCU",
        "OKTA", "ZM", "TWLO", "RNG", "BOX", "ASAN", "TTD", "APP", "ROKU",
        "PINS", "SNAP", "META", "IBM", "CSCO", "CDNS", "SNPS", "ADSK", "INTU"
    ],
    "Misc": [
        "AAPL", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "BLK", "BX", "KKR", "SCHW",
        "KO", "PEP", "PG", "JNJ", "MCD", "SBUX", "COST", "WMT", "TGT", "UNH", "ABBV",
        "MRK", "PFE", "TMUS", "VZ", "T", "CMCSA", "CHTR", "DIS", "NFLX", "BKNG", "MAR",
        "HLT", "UBER", "DASH", "ABNB", "SHOP", "MELI", "PDD", "BABA", "JD", "EA", "TTWO",
        "RBLX", "PYPL", "HOOD", "COIN", "SE", "SPOT", "LYFT"
    ]
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
