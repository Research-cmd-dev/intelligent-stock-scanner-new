"""Tests for src/data/fetcher.py cache behavior (Task 1 coverage check)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.config import get_settings
from src.data import fetcher
from src.utils import get_current_utc_date


def _make_short_frame(n_days: int = 100) -> pd.DataFrame:
    """Create a short recent daily frame ending today (UTC)."""
    today = get_current_utc_date()
    dates = pd.date_range(end=pd.Timestamp(today), periods=n_days, freq="B")  # business days
    df = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000_000,
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )
    return df


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Isolate cache dir and clear settings cache for the test."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("CACHE_DIR", str(cache_dir))
    # Clear lru_cache on get_settings so env change is picked up.
    from src.config import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    # Also ensure fetcher sees fresh settings
    fetcher.get_settings.cache_clear() if hasattr(fetcher, "get_settings") else None
    yield cache_dir
    # cleanup not strictly needed (tmp_path)


def test_cache_rejected_on_insufficient_coverage(monkeypatch, isolated_cache):
    """A today-fresh but short parquet must be rejected for a long lookback request.

    Acceptance for Task 1: write 100-day cache, request 800 days, assert clients called.
    """
    sym = "TESTCOV"
    short_df = _make_short_frame(100)
    path = isolated_cache / f"{sym}_daily.parquet"
    short_df.to_parquet(path)

    # Ensure mtime is "today" (it will be, since we just wrote it in the test process)
    # Patch the source clients to record calls and return a plausible long frame.
    calls = {"polygon": 0, "yfinance": 0}

    def _fake_polygon(symbol: str, lookback_days: int):
        calls["polygon"] += 1
        # Return a longer frame (simulated)
        today = get_current_utc_date()
        long_dates = pd.date_range(end=pd.Timestamp(today), periods=800, freq="B")
        return pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1_000_000},
            index=pd.DatetimeIndex(long_dates, name="date"),
        )

    def _fake_yfinance(symbol: str, lookback_days: int):
        calls["yfinance"] += 1
        today = get_current_utc_date()
        long_dates = pd.date_range(end=pd.Timestamp(today), periods=800, freq="B")
        return pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1_000_000},
            index=pd.DatetimeIndex(long_dates, name="date"),
        )

    monkeypatch.setattr(fetcher.polygon_client, "fetch_daily", _fake_polygon)
    monkeypatch.setattr(fetcher.yfinance_client, "fetch_daily", _fake_yfinance)

    # Force yfinance path (no Polygon key in this env) or just let preference run.
    # The test env typically has no POLYGON key, so yfinance path is taken.
    # We just need to prove the cache was *not* accepted.
    df = fetcher.fetch_ohlcv(sym, lookback_days=800, use_cache=True)

    # The short cache should have been rejected → a client was called.
    assert calls["polygon"] + calls["yfinance"] >= 1, "cache coverage check failed to force refetch"
    assert len(df) > 100, "should have received the longer fetched frame"
    # Also, the new long frame should have been written back to cache
    assert path.exists()
    written = pd.read_parquet(path)
    assert len(written) > 100
