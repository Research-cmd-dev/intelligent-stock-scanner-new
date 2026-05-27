"""
Curated Thematic Universe for the Stock Finder Agent

This module defines the core set of US-listed tickers used across the entire
project, including:

- Scanner & pattern detection
- Narrative layer (themes + catalysts)
- Backtesting and feature evaluation
- Modal remote compute jobs (downloads + backtests)
- Max-history yfinance downloader (`src/data/max_history.py`) — single source of
  truth; that module imports `SECTOR_TICKERS` from here.

Universe shape
--------------
16 sectors, ~800 entries (with intentional cross-sector overlap), 560 unique
tickers. Eight large-cap-led "majors" (AI, Chips, Energy, Space, Robotics,
Bio, Software, Misc) paired with eight smaller/speculative "_micro" variants
(AI_micro, Chips_micro, Energy_micro, Space_micro, Robotics_micro, Bio_micro,
Software_micro, Misc_micro). The micro lanes carry the quantum, nuclear,
batteries, fintech, and crypto-miner exposure that used to live in standalone
sectors.

Editing Guidelines
------------------
- Quality > Quantity
- Stocks must have genuine, strong relevance to the sector/theme
- Prefer liquid, established companies in the majors; the `_micro` lanes are
  the right home for smaller, more speculative names.
- Cross-sector overlap is intentional (e.g. NVDA in AI + Chips); helpers
  deduplicate before use.
- MOG.A is kept in its true ticker form ("." not "-"). Consumers that need
  a filesystem-safe name translate dots to dashes themselves (see
  `src/data/max_history.py::_ticker_to_path`).

Last Updated: 2026-05-21 (synced to 16-sector / 560-unique universe)
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

# Primary data structure: Sector → list of US-listed tickers.
SECTOR_TICKERS: dict[str, list[str]] = {
    "AI": [
        "NVDA", "AVGO", "AMD", "TSM", "ARM", "MU", "MRVL", "QCOM", "ASML", "AMAT", "LRCX",
        "KLAC", "CDNS", "SNPS", "MSFT", "GOOGL", "AMZN", "META", "ORCL", "CRM", "IBM",
        "PLTR", "NOW", "SNOW", "DDOG", "NET", "MDB", "ESTC", "CRWD", "PANW", "FTNT", "ZS",
        "ANET", "CIEN", "FFIV", "CSCO", "NTAP", "PSTG", "WDC", "STX", "SMCI", "DELL", "HPE",
        "VRT", "ETN", "GEV", "PATH", "APP", "TSLA", "AI",
    ],
    "Chips": [
        "NVDA", "AVGO", "AMD", "TSM", "ASML", "INTC", "QCOM", "MRVL", "MU", "ARM", "ADI",
        "TXN", "NXPI", "MCHP", "ON", "SWKS", "MPWR", "MTSI", "POWI", "SLAB", "CRUS", "DIOD",
        "SYNA", "SMTC", "LSCC", "QRVO", "ALGM", "VSH", "LFUS", "AMAT", "LRCX", "KLAC",
        "TER", "ENTG", "KLIC", "ONTO", "NVMI", "AEHR", "ACLS", "FORM", "UCTT", "ICHR",
        "VECO", "PLAB", "CAMT", "COHU", "AMKR", "SIMO", "COHR", "RMBS",
    ],
    "Energy": [
        "VST", "CEG", "TLN", "NRG", "EXC", "XEL", "AEP", "DUK", "NEE", "SO", "D", "ED",
        "FE", "PEG", "ETR", "CNP", "AES", "DTE", "SRE", "AEE", "WEC", "PCG", "OKLO", "SMR",
        "BWXT", "LEU", "CCJ", "UEC", "UUUU", "NXE", "DNN", "NNE", "PWR", "EME", "FIX",
        "APG", "ACM", "J", "KBR", "TTEK", "FLR", "PRIM", "MTZ", "ETN", "GEV", "HUBB", "ENS",
        "NXT", "BEPC", "FSLR",
    ],
    "Space": [
        "RKLB", "ASTS", "LUNR", "RDW", "PL", "SPIR", "BKSY", "SATL", "SIDU", "SPCE", "MNTS",
        "VOYG", "FLY", "KRGN", "MDA", "NOC", "LMT", "RTX", "GD", "BA", "LHX", "HII", "TDG",
        "HEI", "TDY", "KTOS", "MRCY", "AVAV", "CW", "DRS", "ESLT", "HXL", "BWXT", "ACHR",
        "JOBY", "BLDE", "EVTL", "RCAT", "ONDS", "EH", "UMAC", "IRDM", "GSAT", "VSAT",
        "SATS", "PSN", "CACI", "SAIC", "BAH", "LDOS",
    ],
    "Robotics": [
        "ISRG", "ABB", "SYM", "PATH", "IRBT", "SERV", "RR", "KSCP", "ROK", "EMR", "AME",
        "ITW", "DOV", "NDSN", "MIDD", "GTLS", "IEX", "ITT", "RBC", "CR", "GGG", "LECO",
        "FELE", "JBT", "ATS", "GTES", "CGNX", "FARO", "NOVT", "ZBRA", "KEYS", "MBLY", "AUR",
        "LAZR", "OUST", "AEVA", "INVZ", "ARBE", "SSYS", "DDD", "NNDM", "MTLS", "XMTR",
        "PRLB", "TER", "PRCT", "INSP", "RXST", "HLIO", "KAI",
    ],
    "Bio": [
        "LLY", "MRK", "PFE", "JNJ", "ABBV", "BMY", "AZN", "NVO", "SNY", "GSK", "RHHBY",
        "AMGN", "GILD", "VRTX", "REGN", "BIIB", "MRNA", "BNTX", "ILMN", "PACB", "TWST",
        "TXG", "SRPT", "EXEL", "INCY", "NBIX", "UTHR", "JAZZ", "ALKS", "FOLD", "ALNY",
        "INSM", "ASND", "HALO", "CRSP", "NTLA", "BEAM", "EDIT", "RCKT", "ABCL", "PTCT",
        "KRYS", "ARWR", "IONS", "MDGL", "VKTX", "AKRO", "AXSM", "RVMD", "NUVL",
    ],
    "Software": [
        "MSFT", "GOOGL", "AMZN", "ORCL", "META", "IBM", "CSCO", "CRM", "ADBE", "SAP",
        "INTU", "ADSK", "NOW", "WDAY", "TEAM", "HUBS", "SNOW", "DDOG", "NET", "MDB", "ESTC",
        "CFLT", "CRWD", "PANW", "FTNT", "ZS", "S", "GTLB", "FROG", "DT", "DOCU", "OKTA",
        "ZM", "TWLO", "RNG", "BOX", "ASAN", "PLTR", "PATH", "AI", "UPST", "TTD", "APP",
        "ROKU", "PINS", "SNAP", "RBLX", "CDNS", "SNPS", "BSY",
    ],
    "Misc": [
        "AAPL", "JPM", "BAC", "WFC", "GS", "MS", "SCHW", "V", "MA", "BLK", "BX", "KKR",
        "KO", "PEP", "PG", "COST", "WMT", "TGT", "MCD", "SBUX", "UNH", "MRK", "PFE", "JNJ",
        "TMUS", "VZ", "T", "CMCSA", "DIS", "NFLX", "SPOT", "BKNG", "MAR", "HLT", "UBER",
        "LYFT", "ABNB", "DASH", "SHOP", "MELI", "PDD", "BABA", "JD", "SE", "EA", "TTWO",
        "SQ", "PYPL", "HOOD", "COIN",
    ],
    "AI_micro": [
        "SOUN", "BBAI", "AI", "TEM", "RXRX", "SDGR", "VERI", "DOMO", "INOD", "CRNC", "NBIS",
        "APLD", "CRDO", "ALAB", "DOCN", "POWL", "AMSC", "AGYS", "PRGS", "DGII", "IONQ",
        "RGTI", "QBTS", "ARQQ", "QUBT", "PATH", "SERV", "AUR", "MBLY", "INVZ", "ARBE",
        "AEVA", "LAZR", "OUST", "CGNT", "UPST", "LMND", "EVLV", "KVYO", "BRZE", "AMPL",
        "PD", "FROG", "PRO", "NCNO", "CXM", "WK", "BIGC", "CLBT", "INTA",
    ],
    "Chips_micro": [
        "AEHR", "ACLS", "FORM", "UCTT", "ONTO", "NVMI", "CAMT", "KLIC", "COHU", "ICHR",
        "VECO", "PLAB", "ENTG", "SLAB", "CRUS", "DIOD", "MTSI", "POWI", "SYNA", "SMTC",
        "LSCC", "QRVO", "SWKS", "ALGM", "VSH", "LFUS", "WOLF", "NVTS", "TGAN", "AXTI",
        "SIMO", "RMBS", "PENG", "IMOS", "AMKR", "TSEM", "CEVA", "PI", "SITM", "ATOM",
        "QUIK", "HIMX", "MX", "INDI", "AOSL", "EMKR", "PXLW", "CRDO", "KN", "VLN",
    ],
    "Energy_micro": [
        "OKLO", "SMR", "LEU", "BWXT", "NNE", "ASPI", "UEC", "UUUU", "NXE", "DNN", "URG",
        "LTBR", "POWL", "NXT", "AMSC", "FLNC", "STEM", "HASI", "ENS", "ENPH", "SEDG",
        "ARRY", "SHLS", "RUN", "CSIQ", "JKS", "AMPS", "BE", "PLUG", "BLDP", "FCEL", "CLNE",
        "CWEN", "AY", "ORA", "BEPC", "PRIM", "MYRG", "MTZ", "CRGY", "PR", "CIVI", "CRC",
        "AESI", "PARR", "CVI", "CEIX", "AROC", "KGS", "WTTR",
    ],
    "Space_micro": [
        "LUNR", "RDW", "PL", "SPIR", "BKSY", "SATL", "SIDU", "MNTS", "SPCE", "IRDM", "GSAT",
        "VSAT", "SATS", "VOYG", "KRGN", "FLY", "MDA", "KTOS", "AVAV", "RCAT", "UMAC",
        "ONDS", "JOBY", "ACHR", "EH", "BLDE", "EVTL", "MRCY", "ATRO", "AIR", "CDRE", "VVX",
        "DRS", "ESLT", "HXL", "WWD", "ESE", "PSN", "CACI", "SAIC", "HII", "TGI", "MOG.A",
        "CW", "BWXT", "NPK", "ARLO", "ATEX", "AILE", "GILT",
    ],
    "Robotics_micro": [
        "SERV", "RR", "KSCP", "PATH", "IRBT", "FARO", "NOVT", "CGNX", "ZBRA", "MBLY", "AUR",
        "LAZR", "OUST", "AEVA", "INVZ", "ARBE", "NNDM", "SSYS", "DDD", "DM", "MTLS", "MKFG",
        "VLD", "XMTR", "PRLB", "JBT", "MIDD", "NDSN", "GTLS", "ATS", "HLIO", "KAI", "ESE",
        "AOS", "WTS", "MWA", "ITRI", "BMI", "RBC", "LECO", "ATKR", "AZZ", "NPO", "TEX",
        "OSK", "ALLE", "PRCT", "INSP", "RXST", "NVEE",
    ],
    "Bio_micro": [
        "CRSP", "NTLA", "BEAM", "EDIT", "RCKT", "SRPT", "EXEL", "INCY", "PTCT", "NBIX",
        "UTHR", "FOLD", "KRYS", "JAZZ", "ALKS", "ARWR", "IONS", "RNA", "NUVL", "RLAY",
        "RVMD", "CRNX", "MDGL", "AKRO", "VKTX", "AXSM", "SUPN", "DNLI", "RYTM", "HRMY",
        "ALEC", "INSM", "ASND", "HALO", "CORT", "PCVX", "ABCL", "RGNX", "FATE", "IOVA",
        "CYTK", "BCRX", "PRTA", "KROS", "KYMR", "JANX", "PCRX", "RXRX", "SDGR", "RIGL",
    ],
    "Software_micro": [
        "AI", "PATH", "SOUN", "BBAI", "BRZE", "AMPL", "KVYO", "CXM", "SMWB", "FROG", "PD",
        "GTLB", "DT", "ESTC", "S", "QLYS", "TENB", "RPD", "VRNS", "CLBT", "JAMF", "CFLT",
        "DOMO", "VERI", "NCNO", "ALKT", "BL", "WK", "PEGA", "APPN", "INTA", "ALRM", "DSGX",
        "CWAN", "BIGC", "BOX", "DOCN", "EVCM", "EXFY", "ASAN", "FIVN", "BSY", "PRGS", "PRO",
        "INOD", "DGII", "NABL", "CRNC", "PCTY", "WIX",
    ],
    "Misc_micro": [
        "SOFI", "AFRM", "UPST", "LMND", "ROOT", "STNE", "PAGS", "DLO", "OPRA", "MARA",
        "RIOT", "CLSK", "WULF", "CIFR", "HUT", "BTBT", "IREN", "TIGR", "FUTU", "SNAP",
        "ROKU", "MTCH", "BMBL", "ETSY", "RVLV", "CART", "WIX", "CHWY", "YELP", "TREE",
        "LYFT", "TRIP", "PENN", "OPEN", "Z", "RDFN", "COMP", "RKT", "CROX", "YETI", "CAVA",
        "SG", "WRBY", "BROS", "PLNT", "FIGS", "PTON", "ZIP", "ARKO", "RUM",
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
