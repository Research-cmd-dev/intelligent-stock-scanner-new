"""Polygon.io aggregates fetcher.

Returns daily OHLCV bars as a DataFrame indexed by tz-naive date with columns
``open, high, low, close, volume``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from src.config import get_settings
from src.utils import get_current_utc_date


class PolygonError(RuntimeError):
    """Raised when Polygon is unavailable, unauthorized, or returns no data."""


_BASE = "https://api.polygon.io"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=8),
    reraise=True,
)
def _get(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code in (401, 403):
        raise PolygonError(f"unauthorized ({resp.status_code})")
    if resp.status_code == 429:
        raise PolygonError("rate-limited")
    resp.raise_for_status()
    return resp.json()


def fetch_daily(
    symbol: str, lookback_days: int = 400, *, end_date: date | None = None
) -> pd.DataFrame:
    """Daily OHLCV for ``symbol`` over the last ``lookback_days`` calendar days.

    If ``end_date`` is provided, the right edge is anchored to that date
    (for reproducible backtests with a historical ``end``). When None,
    defaults to today (UTC) to preserve live behavior.
    """
    settings = get_settings()
    if not settings.has_polygon:
        raise PolygonError("POLYGON_API_KEY not set")

    end = end_date or get_current_utc_date()
    start = end - timedelta(days=lookback_days)
    url = (
        f"{_BASE}/v2/aggs/ticker/{symbol.upper()}"
        f"/range/1/day/{start.isoformat()}/{end.isoformat()}"
    )
    payload = _get(
        url,
        params={
            "adjusted": "true",
            "sort": "asc",
            "limit": 50_000,
            "apiKey": settings.polygon_api_key,
        },
    )

    results = payload.get("results") or []
    if not results:
        raise PolygonError(f"no data for {symbol}")

    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["t"], unit="ms").dt.tz_localize(None).dt.normalize()
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]].sort_index()
    df.attrs["source"] = "polygon"
    return df
