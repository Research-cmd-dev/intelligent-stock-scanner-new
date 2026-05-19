"""Unit tests for the durable historical store.

Network is fully mocked — the fake fetcher returns synthetic OHLCV
shaped exactly like ``polygon_client.fetch_daily``. We exercise the
end-to-end flow (create → incremental update → unchanged) and the
panel / cache-bridge helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.data import historical


# ---------------------------------------------------------------------- #
# Fakes                                                                  #
# ---------------------------------------------------------------------- #


def _frame(end_offset_days: int = 0, n: int = 30) -> pd.DataFrame:
    """Synthetic OHLCV ending at today - ``end_offset_days``."""
    today = pd.Timestamp(datetime.now(tz=timezone.utc).date())
    end = today - pd.Timedelta(days=end_offset_days)
    idx = pd.bdate_range(end=end, periods=n)
    idx.name = "date"
    return pd.DataFrame(
        {
            "open":   [100.0 + i for i in range(n)],
            "high":   [101.0 + i for i in range(n)],
            "low":    [ 99.0 + i for i in range(n)],
            "close":  [100.5 + i for i in range(n)],
            "volume": [1_000_000 + i * 100 for i in range(n)],
        },
        index=idx,
    )


class _FakeFetcher:
    """Stand-in for ``_fetch_with_fallback`` — records calls + returns a frame."""

    def __init__(self, frame: pd.DataFrame, source: str = "polygon") -> None:
        self.frame = frame
        self.source = source
        self.calls: list[tuple[str, int]] = []

    def __call__(self, symbol: str, lookback_days: int) -> tuple[pd.DataFrame, str]:
        self.calls.append((symbol, lookback_days))
        return self.frame, self.source


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """Point both the historical root and the cache_dir at a temp directory."""
    root = tmp_path / "historical"
    cache = tmp_path / "cache"
    monkeypatch.setenv("STOCK_DATA_ROOT", str(root))
    monkeypatch.setenv("CACHE_DIR", str(cache))
    # settings is lru-cached; clear so the new env vars are picked up.
    from src.config import settings as settings_mod
    settings_mod.get_settings.cache_clear()
    yield root
    settings_mod.get_settings.cache_clear()


# ---------------------------------------------------------------------- #
# Tests                                                                  #
# ---------------------------------------------------------------------- #


def test_load_history_returns_empty_when_missing(isolated_root):
    df = historical.load_history("NOSUCH")
    assert df.empty
    assert list(df.columns) == list(historical.OHLCV_COLUMNS)


def test_update_symbol_creates_then_is_unchanged(isolated_root, monkeypatch):
    fetcher = _FakeFetcher(_frame())
    monkeypatch.setattr(historical, "_fetch_with_fallback", fetcher)

    first = historical.update_symbol("nvda")
    assert first.status == "created"
    assert first.new_rows == 30
    assert first.total_rows == 30
    assert first.source == "polygon"
    assert historical.symbol_path("NVDA").exists()

    # Second call with the same frame should be a no-op merge.
    second = historical.update_symbol("NVDA")
    assert second.status in {"unchanged"}
    assert second.new_rows == 0
    assert second.total_rows == 30


def test_update_symbol_appends_new_bars(isolated_root, monkeypatch):
    # First load: a frame ending 10 days ago.
    initial = _FakeFetcher(_frame(end_offset_days=10, n=30))
    monkeypatch.setattr(historical, "_fetch_with_fallback", initial)
    first = historical.update_symbol("PLTR")
    assert first.status == "created"

    # Incremental fetch returns a frame that ends today and overlaps the tail.
    follow = _FakeFetcher(_frame(end_offset_days=0, n=15))
    monkeypatch.setattr(historical, "_fetch_with_fallback", follow)
    second = historical.update_symbol("PLTR")
    assert second.status == "updated"
    assert second.new_rows > 0
    # And the underlying parquet contains the union with no duplicate index.
    df = historical.load_history("PLTR")
    assert df.index.is_monotonic_increasing
    assert df.index.is_unique


def test_update_symbol_records_error_without_raising(isolated_root, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(historical, "_fetch_with_fallback", boom)
    result = historical.update_symbol("FAIL")
    assert result.status == "error"
    assert "network down" in (result.error or "")


def test_download_universe_aggregates(isolated_root, monkeypatch):
    fetcher = _FakeFetcher(_frame())
    monkeypatch.setattr(historical, "_fetch_with_fallback", fetcher)

    report = historical.download_universe(["NVDA", "PLTR", "AMD"])
    assert {r.symbol for r in report.results} == {"NVDA", "PLTR", "AMD"}
    assert report.created == 3
    assert report.errors == 0
    assert report.total_new_rows == 90


def test_load_panel_dict_and_wide(isolated_root, monkeypatch):
    monkeypatch.setattr(historical, "_fetch_with_fallback", _FakeFetcher(_frame()))
    historical.update_symbol("NVDA")
    historical.update_symbol("PLTR")

    by_symbol = historical.load_panel(["NVDA", "PLTR"])
    assert isinstance(by_symbol, dict)
    assert set(by_symbol) == {"NVDA", "PLTR"}
    assert all(not df.empty for df in by_symbol.values())

    wide_close = historical.load_panel(["NVDA", "PLTR"], field_name="close")
    assert isinstance(wide_close, pd.DataFrame)
    assert set(wide_close.columns) == {"NVDA", "PLTR"}


def test_load_panel_rejects_bad_field(isolated_root):
    with pytest.raises(ValueError):
        historical.load_panel(["NVDA"], field_name="not_a_column")


def test_warm_cache_from_historical_copies_and_touches(isolated_root, monkeypatch):
    monkeypatch.setattr(historical, "_fetch_with_fallback", _FakeFetcher(_frame()))
    historical.update_symbol("NVDA")

    copied = historical.warm_cache_from_historical(["NVDA", "DOES_NOT_EXIST"])
    assert copied == 1

    from src.config import get_settings
    cache_file = get_settings().cache_dir / "NVDA_daily.parquet"
    assert cache_file.exists()
    # mtime is today so the fetcher's freshness check will consider it a hit.
    mtime_date = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=timezone.utc).date()
    assert mtime_date == datetime.now(tz=timezone.utc).date()


def test_normalize_handles_tz_aware_index():
    raw = _frame()
    raw.index = raw.index.tz_localize("UTC")
    normalized = historical._normalize_frame(raw)
    assert normalized.index.tz is None
    assert normalized.index.name == "date"
    assert list(normalized.columns) == list(historical.OHLCV_COLUMNS)


def test_available_tools_catalog_shape():
    """The opt-in tool catalog must be import-safe and well-formed.

    The dashboard / future LangGraph agent reads this without modal
    installed, so we sanity-check shape but never import the app
    module itself.
    """
    from src.modal_app import available_tools

    tools = available_tools()
    assert {t["name"] for t in tools} == {"download_historical_data", "run_backtest"}
    for tool in tools:
        assert tool["function_path"].startswith("src.modal_app.app:")
        assert tool["parameters"]["type"] == "object"
