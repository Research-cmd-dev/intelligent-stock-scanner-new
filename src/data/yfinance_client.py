"""yfinance fallback fetcher.

Returns daily OHLCV bars in the same canonical format as ``polygon_client``.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf


class YFinanceError(RuntimeError):
    pass


def fetch_daily(symbol: str, lookback_days: int = 400) -> pd.DataFrame:
    """Download daily bars via yfinance."""
    period = _period_for(lookback_days)
    try:
        df = yf.download(
            symbol,
            period=period,
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


def _period_for(lookback_days: int) -> str:
    if lookback_days <= 30:
        return "1mo"
    if lookback_days <= 90:
        return "3mo"
    if lookback_days <= 180:
        return "6mo"
    if lookback_days <= 365:
        return "1y"
    if lookback_days <= 730:
        return "2y"
    return "5y"
