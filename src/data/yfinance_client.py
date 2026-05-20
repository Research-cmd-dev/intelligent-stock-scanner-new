"""yfinance fallback fetcher.

Returns daily OHLCV bars in the same canonical format as ``polygon_client``.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import yfinance as yf

from src.utils import get_current_utc_date


class YFinanceError(RuntimeError):
    pass


def fetch_daily(symbol: str, lookback_days: int = 400) -> pd.DataFrame:
    """Download daily bars via yfinance using an explicit start date.

    Supports arbitrary lookback (10y, 20y+, or "all available history" for a ticker).
    When the requested window predates a symbol's listing date, yfinance simply
    returns whatever data exists — the historical store and backtest layers
    handle shorter histories gracefully.
    """
    end = get_current_utc_date()
    start = end - timedelta(days=lookback_days)

    try:
        df = yf.download(
            symbol,
            start=start.isoformat(),
            end=None,  # fetch up to the most recent available trading day
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
