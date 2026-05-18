"""Alpha158-style features in pandas.

This module implements a representative ~20-feature subset of the
Qlib Alpha158 factor set. Every feature is computed directly from a
single symbol's OHLCV frame — no Qlib runtime required — so the system
works out of the box even when ``pyqlib`` isn't installed.

The Qlib path is supported as an *enhancement*: call
:func:`try_qlib_alpha158` to opt into Qlib's actual ``Alpha158``
handler. That requires (1) ``pyqlib`` importable and (2) a populated
Qlib bin-data directory. When either is missing the function returns
``None`` and callers fall back to the pandas features here.

The feature names match Qlib's convention so a swap to the real
handler in the future doesn't break downstream code:

    KMID, KLEN, KUP, KLOW, KMID2, KSFT
    ROC5, ROC10, ROC20, ROC60
    MA5, MA20, MA60          (close / MA_n − 1)
    STD5, STD20              (rolling std of daily returns)
    BETA20                   (slope of 20-bar OLS regression on time)
    VMA5, VMA20              (volume / vol-MA_n − 1)
    IMAX5, IMIN5             (fraction-into-window of last 5-bar high/low)
    QTLU20                   (close / rolling-20 high − 1)
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from src.utils import get_logger

log = get_logger(__name__)


# Canonical feature list. Exposed as a constant so the evaluator and
# refinement heuristics can iterate without hard-coding strings.
ALPHA158_LITE_FEATURES: tuple[str, ...] = (
    "KMID", "KLEN", "KUP", "KLOW", "KMID2", "KSFT",
    "ROC5", "ROC10", "ROC20", "ROC60",
    "MA5", "MA20", "MA60",
    "STD5", "STD20",
    "BETA20",
    "VMA5", "VMA20",
    "IMAX5", "IMIN5",
    "QTLU20",
)


def compute_alpha158_lite(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame indexed by ``df.index`` with one column per feature.

    ``df`` is the canonical OHLCV frame (open/high/low/close/volume on a
    DatetimeIndex) — the same shape ``src.data.fetcher`` produces. Rows
    where a feature's lookback isn't satisfied stay NaN; the evaluator
    drops them when computing the IC, so there's no need to fill.
    """
    _validate(df)

    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    ret = close.pct_change()

    out = pd.DataFrame(index=df.index)

    # ---- K-line features: shape of the daily candle ------------------- #
    # Match Qlib's normalization (divide by open / close) so the values
    # are unit-free and comparable across price levels.
    out["KMID"] = (close - open_) / open_
    out["KLEN"] = (high - low) / open_
    out["KUP"] = (high - np.maximum(open_, close)) / open_
    out["KLOW"] = (np.minimum(open_, close) - low) / open_
    out["KMID2"] = (close - open_) / (high - low).replace(0, np.nan)
    out["KSFT"] = (2 * close - high - low) / open_

    # ---- Rate-of-change features -------------------------------------- #
    for n in (5, 10, 20, 60):
        out[f"ROC{n}"] = close.pct_change(n)

    # ---- Moving-average distance -------------------------------------- #
    for n in (5, 20, 60):
        ma = close.rolling(n, min_periods=n).mean()
        out[f"MA{n}"] = close / ma - 1.0

    # ---- Volatility (rolling std of daily returns) -------------------- #
    out["STD5"] = ret.rolling(5, min_periods=5).std()
    out["STD20"] = ret.rolling(20, min_periods=20).std()

    # ---- BETA20: OLS slope of close vs time over a 20-bar window ------ #
    # Closed-form to avoid scipy. cov(x,y)/var(x) where x is 0..n-1.
    out["BETA20"] = _rolling_slope(close, window=20)

    # ---- Volume features ---------------------------------------------- #
    for n in (5, 20):
        vma = volume.rolling(n, min_periods=n).mean()
        out[f"VMA{n}"] = volume / vma - 1.0

    # ---- Position of extreme inside the last N bars ------------------- #
    # IMAX5 = (5 − bars-since-high) / 5 → 1.0 when today is the 5-bar high.
    out["IMAX5"] = _position_of_max(close, window=5)
    out["IMIN5"] = _position_of_min(close, window=5)

    # ---- Position vs rolling-20 high ---------------------------------- #
    out["QTLU20"] = close / close.rolling(20, min_periods=20).max() - 1.0

    return out


# ---------------------------------------------------------------------- #
# Optional Qlib pass-through                                              #
# ---------------------------------------------------------------------- #


def try_qlib_alpha158(
    symbols: Iterable[str],
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    provider_uri: str | None = None,
) -> pd.DataFrame | None:
    """Return the real Qlib Alpha158 panel when available, ``None`` otherwise.

    ``provider_uri`` is the Qlib data root (the directory you'd pass to
    ``qlib.init(provider_uri=...)``). If ``None``, this function tries
    ``~/.qlib/qlib_data/us_data`` and ``~/.qlib/qlib_data/cn_data`` as
    sensible defaults — both fail cleanly when missing, so callers can
    rely on a ``None`` return to mean "fall back to pandas features."
    """
    try:
        import qlib  # noqa: F401
        from qlib.contrib.data.handler import Alpha158
        from qlib.utils import init_instance_by_config  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        log.info("Qlib unavailable, using pandas Alpha158-lite: %s", exc)
        return None

    from pathlib import Path

    candidates = (
        [provider_uri] if provider_uri
        else [
            Path.home() / ".qlib" / "qlib_data" / "us_data",
            Path.home() / ".qlib" / "qlib_data" / "cn_data",
        ]
    )
    chosen = next((Path(p) for p in candidates if Path(p).exists()), None)
    if chosen is None:
        log.info(
            "Qlib installed but no data dir found in %s; using pandas "
            "Alpha158-lite. Run Qlib's data prep to enable the real handler.",
            [str(c) for c in candidates],
        )
        return None

    try:
        qlib.init(provider_uri=str(chosen))
        handler = Alpha158(
            instruments=list(symbols),
            start_time=str(pd.Timestamp(start).date()),
            end_time=str(pd.Timestamp(end).date()),
        )
        return handler.fetch()
    except Exception as exc:  # noqa: BLE001
        log.warning("Qlib Alpha158 fetch failed (%s); falling back to pandas.", exc)
        return None


# ---------------------------------------------------------------------- #
# Internals                                                              #
# ---------------------------------------------------------------------- #


def _validate(df: pd.DataFrame) -> None:
    missing = [c for c in ("open", "high", "low", "close", "volume") if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")


def _rolling_slope(series: pd.Series, *, window: int) -> pd.Series:
    """Per-bar OLS slope of ``series`` on ``[0, 1, …, window-1]``."""
    if window < 2:
        raise ValueError("window must be >= 2")
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(values: np.ndarray) -> float:
        # Manually drop NaNs so we don't get spurious slopes during warmup.
        v = values[~np.isnan(values)]
        if len(v) < window:
            return np.nan
        y_mean = v.mean()
        return ((x - x_mean) * (v - y_mean)).sum() / x_var

    return series.rolling(window, min_periods=window).apply(_slope, raw=True)


def _position_of_max(series: pd.Series, *, window: int) -> pd.Series:
    """1.0 when today is the window's high, 0.0 when it's the oldest bar's high."""
    def _f(values: np.ndarray) -> float:
        if np.isnan(values).any():
            return np.nan
        idx = int(np.argmax(values))
        return idx / (window - 1) if window > 1 else 0.0
    return series.rolling(window, min_periods=window).apply(_f, raw=True)


def _position_of_min(series: pd.Series, *, window: int) -> pd.Series:
    def _f(values: np.ndarray) -> float:
        if np.isnan(values).any():
            return np.nan
        idx = int(np.argmin(values))
        return idx / (window - 1) if window > 1 else 0.0
    return series.rolling(window, min_periods=window).apply(_f, raw=True)
