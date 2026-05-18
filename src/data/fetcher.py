"""Unified OHLCV fetcher.

- Tries Polygon first when an API key is configured.
- Falls back to yfinance on any Polygon failure (auth, quota, missing data).
- Caches results to ``data/cache/{symbol}_daily.parquet``; cache is considered
  fresh if the latest row is from the current calendar day (UTC).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.config import get_settings
from src.utils import get_logger

from . import polygon_client, yfinance_client

log = get_logger(__name__)


def _cache_path(symbol: str) -> Path:
    return get_settings().cache_dir / f"{symbol.upper()}_daily.parquet"


def _cache_is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()
    return mtime == datetime.now(tz=timezone.utc).date()


def fetch_ohlcv(
    symbol: str,
    lookback_days: int = 400,
    *,
    use_cache: bool = True,
    force_source: str | None = None,
) -> pd.DataFrame:
    """Fetch daily OHLCV for ``symbol``.

    ``force_source`` accepts ``"polygon"`` or ``"yfinance"`` to bypass the
    automatic preference; otherwise Polygon is tried first.
    """
    path = _cache_path(symbol)
    if use_cache and force_source is None and _cache_is_fresh(path):
        try:
            df = pd.read_parquet(path)
            log.debug("cache hit %s", symbol)
            return df
        except Exception as exc:  # pragma: no cover - corrupt cache
            log.warning("cache read failed for %s: %s", symbol, exc)

    df = _fetch_from_source(symbol, lookback_days, force_source)

    try:
        df.to_parquet(path)
    except Exception as exc:  # pragma: no cover - disk issues
        log.warning("cache write failed for %s: %s", symbol, exc)

    return df


def _fetch_from_source(
    symbol: str, lookback_days: int, force_source: str | None
) -> pd.DataFrame:
    settings = get_settings()
    sources: list[str]
    if force_source:
        sources = [force_source]
    elif settings.has_polygon:
        sources = ["polygon", "yfinance"]
    else:
        sources = ["yfinance"]

    last_error: Exception | None = None
    for source in sources:
        try:
            if source == "polygon":
                return polygon_client.fetch_daily(symbol, lookback_days)
            return yfinance_client.fetch_daily(symbol, lookback_days)
        except Exception as exc:
            log.info("%s fetch failed for %s: %s", source, symbol, exc)
            last_error = exc

    raise RuntimeError(f"all sources failed for {symbol}: {last_error}")


def fetch_many(
    symbols: list[str], lookback_days: int = 400
) -> dict[str, pd.DataFrame]:
    """Fetch a batch of symbols; symbols that fail are simply omitted."""
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            out[sym] = fetch_ohlcv(sym, lookback_days)
        except Exception as exc:
            log.warning("dropping %s: %s", sym, exc)
    return out
