"""Modal application: persistent OHLCV volume + remote heavy jobs.

Two remote functions live here:

* :func:`download_universe_remote` — refreshes the ``stock_data``
  volume with daily OHLCV for thousands of symbols in parallel.
* :func:`run_backtest_remote` — executes the full backtest +
  optional feature-evaluation pipeline using data already in the
  volume, writes the markdown report to ``/data/backtests/<run>/``,
  and returns a compact summary the local caller can render.

Design notes
------------

* The container image installs the project's full ``requirements.txt``
  and adds the ``src`` tree as Python source — so the very same
  detector / narrative / backtest code that runs in Codespaces runs on
  Modal, byte for byte. No duplication.
* The volume is mounted at ``/data``; the inside-the-container code
  sets ``STOCK_DATA_ROOT=/data/historical`` and
  ``STOCK_BACKTEST_ROOT=/data/backtests``. That env-driven indirection
  is the only Modal-awareness the project's other modules need.
* Secrets are read from a ``.env`` file at job-submission time via
  :func:`modal.Secret.from_dotenv` — solo-dev friendly. Teams that
  prefer a named secret can swap to ``modal.Secret.from_name(...)``;
  the rest of the file is unchanged.
* ``modal`` is imported lazily so the sibling :mod:`src.modal_app`
  package stays importable on machines that don't have the client
  installed (e.g. the Streamlit container).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Lazy import: `modal` is only needed when this module is the actual
# entry point (modal run / modal deploy) or when the local runner
# triggers a remote call. Tests and other src.modal_app users can
# import the catalog without it.
import modal  # noqa: E402  (kept at top because Modal decorators need it)

from src.config import get_settings

# ---------------------------------------------------------------------- #
# Volume + image + app                                                   #
# ---------------------------------------------------------------------- #

VOLUME_NAME = "stock_data"
VOLUME_MOUNT = "/data"
HISTORICAL_DIR = f"{VOLUME_MOUNT}/historical"
BACKTEST_DIR = f"{VOLUME_MOUNT}/backtests"

stock_data_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def _build_secrets() -> list[modal.Secret]:
    """Bundle the local ``.env`` as a Modal Secret if it exists.

    Falls back to an empty list when no ``.env`` is present — the
    yfinance fallback then handles data fetches without keys, which
    is the same degraded mode the local fetcher supports.
    """
    env_path = Path(get_settings().repo_root) / ".env"
    if env_path.exists():
        return [modal.Secret.from_dotenv(path=str(env_path))]
    return []


_REPO_ROOT = Path(__file__).resolve().parents[2]

# Image: project deps + the src tree. ``add_local_python_source`` adds
# Python files at function-import time (not image-build time) so editing
# code does not trigger an image rebuild.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(_REPO_ROOT / "requirements.txt"))
    .add_local_python_source("src")
)

app = modal.App("stock-finder")


def _activate_volume_paths() -> None:
    """Point the historical + backtest helpers at the mounted volume."""
    os.environ["STOCK_DATA_ROOT"] = HISTORICAL_DIR
    os.environ["STOCK_BACKTEST_ROOT"] = BACKTEST_DIR
    os.environ.setdefault("CACHE_DIR", "/tmp/cache")
    os.environ.setdefault("LOG_DIR", BACKTEST_DIR)
    Path(HISTORICAL_DIR).mkdir(parents=True, exist_ok=True)
    Path(BACKTEST_DIR).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------- #
# Remote functions                                                       #
# ---------------------------------------------------------------------- #


@app.function(
    image=image,
    volumes={VOLUME_MOUNT: stock_data_volume},
    secrets=_build_secrets(),
    timeout=60 * 60 * 2,  # 2h — enough for a 5k-symbol cold pull
    cpu=4,
    memory=8192,
)
def download_universe_remote(
    symbols: list[str],
    *,
    force: bool = False,
    max_workers: int = 16,
) -> dict[str, Any]:
    """Refresh the volume's historical store and return a summary dict."""
    _activate_volume_paths()

    # Import here so the module is loaded inside the Modal container,
    # not at decorator definition time on the submitting machine.
    from src.config import get_settings
    get_settings.cache_clear()  # re-read env we just set

    from src.data.historical import download_universe

    report = download_universe(symbols, force=force, max_workers=max_workers)
    stock_data_volume.commit()  # persist writes across invocations
    return report.to_dict()


@app.function(
    image=image,
    volumes={VOLUME_MOUNT: stock_data_volume},
    secrets=_build_secrets(),
    timeout=60 * 60 * 2,
    cpu=4,
    memory=8192,
)
def run_backtest_remote(
    symbols: list[str],
    *,
    start: str,
    end: str,
    min_score: float = 60.0,
    hold_days: int = 20,
    cooldown_days: int = 0,
    evaluate_features: bool = False,
    feature_horizon: int = 5,
    refresh: bool = True,
) -> dict[str, Any]:
    """Run a full backtest using volume-resident OHLCV.

    Steps inside the container:

    1. Optionally refresh missing / stale symbols (default on — safer
       for ad-hoc runs; pass ``refresh=False`` for a strict replay).
    2. Warm ``data/cache`` from the historical store so the existing
       backtest code path is byte-identical to local execution.
    3. Run ``run_backtest`` + ``write_report``.
    4. Persist outputs back to ``/data/backtests/<timestamp>/``.

    Returns the same kind of summary the local CLI prints — counts,
    headline metrics, top-5 features by |IR|, suggestion titles, and
    the volume-relative path to the written markdown.
    """
    _activate_volume_paths()
    from src.config import get_settings
    get_settings.cache_clear()

    from src.backtest import run_backtest
    from src.backtest.report import write_report
    from src.data.historical import download_universe, warm_cache_from_historical

    if refresh:
        download_universe(symbols, force=False, max_workers=16)
        stock_data_volume.commit()

    warm_cache_from_historical(symbols)

    report = run_backtest(
        symbols,
        start=start,
        end=end,
        min_score=min_score,
        hold_days=hold_days,
        cooldown_days=cooldown_days,
        evaluate_features=evaluate_features,
        feature_horizon=feature_horizon,
    )

    run_path, suggestions = write_report(report)
    stock_data_volume.commit()

    metrics = report.metrics
    fe = report.features_evaluation
    return {
        "report_path": str(run_path),
        "signals": len(report.signals),
        "trades": metrics.trade_count,
        "win_rate": metrics.win_rate,
        "mean_return": metrics.mean_return,
        "profit_factor": metrics.profit_factor,
        "max_drawdown": metrics.max_drawdown,
        "sharpe_like": metrics.sharpe_like,
        "params": report.params,
        "top_features": (
            [
                {
                    "name": s.name,
                    "ir": s.ir,
                    "mean_ic": s.mean_ic,
                    "n_periods": s.n_periods,
                    "category": s.category,
                }
                for s in (fe.stats[:5] if fe and fe.stats else [])
            ]
        ),
        "suggestions": [
            {"priority": s.priority, "category": s.category, "title": s.title}
            for s in suggestions
        ],
    }


# ---------------------------------------------------------------------- #
# Local entrypoints — usable from Codespaces with `modal run ...`        #
# ---------------------------------------------------------------------- #


@app.local_entrypoint()
def download(symbols: str, force: bool = False, workers: int = 16) -> None:
    """`modal run src.modal_app.app::download --symbols NVDA,PLTR`."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    summary = download_universe_remote.remote(syms, force=force, max_workers=workers)
    s = summary["summary"]
    print(
        f"created={s['created']} updated={s['updated']} "
        f"unchanged={s['unchanged']} errors={s['errors']} "
        f"new_rows={s['total_new_rows']} elapsed={summary['elapsed_s']}s"
    )


@app.local_entrypoint()
def backtest(
    symbols: str,
    start: str,
    end: str,
    min_score: float = 60.0,
    hold_days: int = 20,
    evaluate_features: bool = False,
) -> None:
    """`modal run src.modal_app.app::backtest --symbols NVDA,PLTR --start 2024-01-01 --end 2026-05-01`."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    out = run_backtest_remote.remote(
        syms, start=start, end=end,
        min_score=min_score, hold_days=hold_days,
        evaluate_features=evaluate_features,
    )
    print(f"\nReport: {out['report_path']}")
    print(f"  signals={out['signals']} trades={out['trades']}")
    if out["trades"]:
        print(
            f"  win_rate={out['win_rate']:.0%} mean_return={out['mean_return']:.2%} "
            f"PF={out['profit_factor']:.2f} dd={out['max_drawdown']:.2%} "
            f"sharpe={out['sharpe_like']:.2f}"
        )
    if out["top_features"]:
        print("\nTop features by |IR|:")
        for f in out["top_features"]:
            print(f"  {f['name']:<22} IR={f['ir']:+.2f} n={f['n_periods']} ({f['category']})")
    if out["suggestions"]:
        print(f"\n{len(out['suggestions'])} suggestion(s):")
        for s in out["suggestions"]:
            print(f"  [{s['priority'].upper()}] {s['category']}: {s['title']}")
