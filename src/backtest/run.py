"""CLI entry point for the backtest layer.

Usage::

    python -m src.backtest.run --start 2024-01-01 --end 2026-05-01
    python -m src.backtest.run --symbols NVDA,PLTR --start 2024-01-01 --end 2026-05-01
    python -m src.backtest.run --sector AI Chips --start 2024-01-01

When no symbols / sectors are given, the full thematic discovery
universe (single stocks only) is used — heavy but the canonical
benchmark.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from src.config.sectors import all_tickers, tickers_for_sectors
from src.utils import get_current_utc_date, get_logger

from .engine import run_backtest
from .report import write_report

log = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    symbols = _resolve_symbols(args)
    if not symbols:
        log.error("no symbols resolved from arguments")
        return 2

    log.info(
        "running backtest: %d symbols, %s → %s, min_score=%g, hold_days=%d, features=%s",
        len(symbols), args.start, args.end, args.min_score, args.hold_days,
        args.evaluate_features,
    )
    report = run_backtest(
        symbols,
        start=args.start,
        end=args.end,
        min_score=args.min_score,
        hold_days=args.hold_days,
        cooldown_days=args.cooldown_days,
        evaluate_features=args.evaluate_features,
        feature_horizon=args.feature_horizon,
    )
    run_path, suggestions = write_report(report)

    # Concise stdout summary so the CLI is useful without opening the file.
    print(f"\nBacktest complete: {run_path}")
    print(f"  signals: {len(report.signals)}  trades: {report.metrics.trade_count}")
    if report.metrics.trade_count:
        m = report.metrics
        print(f"  win rate: {m.win_rate:.0%}   "
              f"mean return: {m.mean_return:.2%}   "
              f"profit factor: {m.profit_factor:.2f}")
        print(f"  max drawdown: {m.max_drawdown:.2%}   "
              f"sharpe-like: {m.sharpe_like:.2f}")
    fe = report.features_evaluation
    if fe is not None and fe.stats:
        print(f"\nTop features by |IR| (horizon={fe.forward_horizon}d):")
        for s in fe.stats[:5]:
            print(f"  {s.name:<22} IR={s.ir:+.2f}  IC={s.mean_ic:+.4f}  "
                  f"n={s.n_periods}  ({s.category})")

    if suggestions:
        print(f"\n{len(suggestions)} suggestion(s):")
        for s in suggestions:
            print(f"  [{s.priority.upper()}] {s.category}: {s.title}")
    else:
        print("\nNo refinement suggestions for this run.")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    today = get_current_utc_date()
    default_start = (today - timedelta(days=365 * 2)).isoformat()

    p = argparse.ArgumentParser(
        prog="python -m src.backtest.run",
        description="Replay the scanner over historical data and report performance.",
    )
    universe = p.add_mutually_exclusive_group()
    universe.add_argument(
        "--symbols",
        help="Comma-separated tickers (e.g. NVDA,PLTR,RKLB).",
    )
    universe.add_argument(
        "--sector", nargs="+", metavar="SECTOR",
        help="One or more sector names from src/config/sectors.py.",
    )

    p.add_argument("--start", default=default_start,
                   help=f"First in-window bar (YYYY-MM-DD). Default: {default_start}")
    p.add_argument("--end", default=today.isoformat(),
                   help="Last in-window bar (YYYY-MM-DD).")
    p.add_argument("--min-score", type=float, default=60.0,
                   help="Pattern-score floor (default 60).")
    p.add_argument("--hold-days", type=int, default=20,
                   help="Forward-holding window in trading days (default 20).")
    p.add_argument("--cooldown-days", type=int, default=0,
                   help="Skip re-entries on the same (symbol, pattern) for N days.")

    p.add_argument("--evaluate-features", action="store_true",
                   help="Also run feature engineering + IC evaluation "
                        "(Alpha158-lite + custom + sector-relative).")
    p.add_argument("--feature-horizon", type=int, default=5,
                   help="Forward-return horizon (bars) for feature IC "
                        "(default 5).")

    return p.parse_args(argv)


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return sorted({s.strip().upper() for s in args.symbols.split(",") if s.strip()})
    if args.sector:
        return tickers_for_sectors(args.sector)
    return all_tickers()


if __name__ == "__main__":
    sys.exit(main())
