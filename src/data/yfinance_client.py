"""yfinance fallback fetcher.

Returns daily OHLCV bars in the same canonical format as ``polygon_client``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from src.utils import get_current_utc_date


class YFinanceError(RuntimeError):
    pass


def fetch_daily(
    symbol: str, lookback_days: int = 400, *, end_date: date | None = None
) -> pd.DataFrame:
    """Download daily bars via yfinance using an explicit start date.

    If ``end_date`` is provided, the right edge is anchored to that date
    (inclusive) for reproducible backtests. When None, defaults to today
    (live behavior).
    """
    end = end_date or get_current_utc_date()
    start = end - timedelta(days=lookback_days)

    # yfinance 'end' param is exclusive, so add one day to include end_date
    yf_end = (end + timedelta(days=1)).isoformat() if end_date is not None else None

    try:
        df = yf.download(
            symbol,
            start=start.isoformat(),
            end=yf_end,  # None means "up to most recent"
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as exc:  # pragma: no cover - network failures
        raise YFinanceError(str(exc)) from exc

    if df is None or df.empty:
        raise YFinanceError(f"no data for {symbol}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    df.attrs["source"] = "yfinance"
    return df
