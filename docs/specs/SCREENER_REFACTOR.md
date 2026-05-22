# Screener Refactor: Forward-Return Evaluation

## Mission

The current `src/backtest/` engine simulates trades (buy/hold/sell) and reports
trading metrics (profit factor, max drawdown, sharpe-like). This is the wrong
framework for the project's actual purpose, which is **idea generation**, not
trading.

Refactor the backtest layer to evaluate the scanner as a **screener**: for each
historical signal, measure the stock's forward returns at multiple horizons,
then report distribution-based metrics (hit rates, return distributions,
excess return vs. a universe baseline). No trade simulation, no stops, no
hold periods, no cooldowns.

The existing trading-mode backtest should remain available behind a flag for
diagnostics, but **screener mode is the new default**.

## What to keep unchanged

- `src/backtest/signals.py` — `generate_signals()` is exactly what we need.
  No changes.
- `src/scanner/` — entire scanner layer untouched.
- `src/narrative/`, `src/features/`, `src/research/` — untouched.
- `src/backtest/engine.py` — keep `simulate_trades()` and its dataclasses.
  Will become the "trading mode" diagnostic.
- `src/backtest/metrics.py` — keep `compute_metrics()` and the trading metrics.
  We will add new screener-specific metrics alongside.

## What to add

### 1. `src/backtest/screener.py` (new file)

Core forward-return measurement. Replaces `simulate_trades()` for screener mode.

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import pandas as pd

from src.backtest.signals import Signal

DEFAULT_HORIZONS_DAYS = (21, 63, 126, 252)   # ~1mo, 3mo, 6mo, 12mo (trading days)
DEFAULT_THRESHOLDS    = (0.10, 0.20, 0.50, 1.00)


@dataclass(frozen=True)
class ForwardOutcome:
    """A signal plus what the stock did afterwards. No trade, no exit, no PnL."""
    signal: Signal
    horizons_days: tuple[int, ...]
    forward_returns: dict[int, float | None]   # horizon -> return; None if truncated
    max_favorable_excursion: float | None      # peak return in longest horizon window
    max_adverse_excursion: float | None        # trough return in longest horizon window
    days_to_peak: int | None                   # trading days from signal to MFE peak
    truncated: bool                            # signal too close to end of available data

    def to_row(self) -> dict: ...


def measure_forward_returns(
    signals: list[Signal],
    *,
    horizons_days: tuple[int, ...] = DEFAULT_HORIZONS_DAYS,
    end: date | None = None,
) -> list[ForwardOutcome]:
    """For each signal, look up the symbol's forward price action and compute returns.

    Implementation:
      - Group signals by symbol so each symbol's OHLCV is fetched once
        (use the unified fetcher; it hits the disk cache).
      - For each signal, locate the bar at `signal.date` in the symbol's DataFrame.
      - Use entry_price = next bar's open (consistent with the trading engine's
        entry convention, so the two modes stay comparable).
      - For each horizon h: forward_returns[h] = close[idx + h] / entry_price - 1.
        If idx + h exceeds available data, set to None and mark truncated.
      - Over the longest-horizon window, compute MFE and MAE from the close series.
      - days_to_peak is the trading-day offset from the signal to where MFE occurred.
      - Missing OHLCV or signal date not in index → return ForwardOutcome with
        all-None returns and truncated=True. Never raise.
    """
    ...
```

### 2. `src/backtest/screener_metrics.py` (new file)

Distribution-based metrics for screener evaluation.

```python
from dataclasses import dataclass
from src.backtest.screener import ForwardOutcome


@dataclass(frozen=True)
class ReturnStats:
    n: int
    mean: float
    median: float
    p05: float
    p25: float
    p75: float
    p95: float
    stdev: float


@dataclass(frozen=True)
class ScreenerMetrics:
    total_signals: int
    truncated_signals: int

    # horizon_days -> ReturnStats over non-null returns
    return_stats: dict[int, ReturnStats]

    # horizon_days -> { threshold -> fraction of signals that hit it }
    # e.g. hit_rates[63][0.20] = 0.31 means 31% of signals gained 20%+ in 63 days
    hit_rates: dict[int, dict[float, float]]

    # horizon_days -> avg universe return over the same calendar period
    # (computed as the cross-sectional mean of all symbols in the scanned
    # universe over the matching horizon, averaged across signal dates).
    baseline_returns: dict[int, float]

    # horizon_days -> mean(signal_returns) - baseline_returns[h]
    # Positive excess means the scanner is surfacing above-baseline movers.
    excess_returns: dict[int, float]

    # Distribution of MFE / MAE for understanding right-tail / left-tail behavior
    mfe_stats: ReturnStats
    mae_stats: ReturnStats


def compute_screener_metrics(
    outcomes: list[ForwardOutcome],
    *,
    universe_returns_by_horizon: dict[int, dict[date, float]] | None = None,
    thresholds: tuple[float, ...] = (0.10, 0.20, 0.50, 1.00),
) -> ScreenerMetrics:
    """Aggregate outcomes into distribution-level statistics.

    universe_returns_by_horizon[h][signal_date] = mean forward return at horizon h
    across the full universe for signals dated on that date. Computed once at
    the caller level (in run.py or screener.py) to avoid re-fetching prices.
    Pass None to skip baseline/excess computation.
    """
    ...


def breakdown_by(
    outcomes: list[ForwardOutcome],
    key: str,                              # "pattern" | "sector" | "score_band"
    *,
    thresholds: tuple[float, ...] = (0.10, 0.20, 0.50, 1.00),
    horizons_days: tuple[int, ...] = (21, 63, 126, 252),
) -> dict[str, ScreenerMetrics]:
    """Group outcomes by key and compute ScreenerMetrics per group.

    For "sector", fan multi-sector signals into every group they touch
    (same convention as the trading metrics breakdown_by_sector).

    For "score_band", use bands [50,60), [60,70), [70,80), [80,90), [90,100].
    """
    ...
```

### 3. `src/backtest/screener_report.py` (new file)

Markdown report writer for screener mode.

```python
def write_screener_report(
    metrics: ScreenerMetrics,
    *,
    pattern_breakdown: dict[str, ScreenerMetrics],
    sector_breakdown: dict[str, ScreenerMetrics],
    score_band_breakdown: dict[str, ScreenerMetrics],
    outcomes: list[ForwardOutcome],     # for top-mover examples
    output_dir: Path,
    params: dict,                       # the run parameters
) -> Path:
    """Write a Markdown screener report to logs/screener_{timestamp}.md.

    Sections:
      1. Run parameters (universe, date range, min_score, horizons, narrative on/off)
      2. Headline numbers:
           - Total signals, truncated count
           - For each horizon: mean / median return, vs baseline, excess
           - Hit rates table (rows = horizons, cols = thresholds)
      3. MFE / MAE distribution summary
      4. Breakdown by pattern (Trend Rider vs Bottom Hunter)
      5. Breakdown by sector
      6. Breakdown by score band (does higher score → better forward returns?)
      7. Top 20 right-tail signals (highest MFE) with date, symbol, pattern,
         score, MFE, days_to_peak — for visual review
      8. Bottom 10 signals (worst forward return) — for understanding failures
    """
    ...
```

### 4. `src/backtest/refine.py` updates

Add screener-mode heuristics. The existing trading heuristics stay for
trading mode. New screener heuristics:

- **Pattern hit-rate gap**: if Trend Rider's 63-day hit rate at 20% is
  meaningfully higher than Bottom Hunter's (or vice versa), suggest
  rebalancing weights or tightening the weaker pattern's gates.
- **Score-band gradient**: if higher score bands don't produce higher
  forward returns, the scoring isn't calibrated. Suggest revisiting
  factor weights.
- **No excess over baseline**: if `excess_returns[63]` (3-month) is near
  zero or negative, the scanner isn't adding value above random — flag
  as `[HIGH]` severity.
- **Right-tail dominance check**: if MFE p95 is >5x the median forward
  return, the scanner is right-tail-driven (good for asymmetric purposes).
  Note this in the report so the user understands the distribution shape.

Each new heuristic must respect the existing `MIN_TOTAL_TRADES_FOR_CONFIDENCE`
gate — use it as `MIN_TOTAL_SIGNALS_FOR_CONFIDENCE`. Thin samples → no
suggestions.

### 5. `src/backtest/run.py` (CLI) updates

Add a `--mode` flag:

```
python -m src.backtest.run --mode screener --start 2020-01-01     # NEW DEFAULT
python -m src.backtest.run --mode trading  --start 2020-01-01     # legacy
```

In screener mode:
- Skip `simulate_trades`, skip `compute_metrics`.
- Call `measure_forward_returns(signals)` → outcomes.
- Compute the universe baseline (mean forward return across the universe
  for each signal date, per horizon) ONCE up front; pass to
  `compute_screener_metrics`.
- Run the three `breakdown_by` calls.
- Write screener report.
- Run `refine.suggest_improvements` with screener heuristics enabled.
- Print one-line summary: signal count, 63d hit rate at 20%, excess return
  vs baseline.

Flags relevant to screener mode:
- `--horizons 21,63,126,252` (override default horizons)
- `--thresholds 0.10,0.20,0.50,1.00` (override hit-rate thresholds)
- `--min-score`, `--sector`, `--symbols`, `--start`, `--end` (unchanged)
- Drop `--hold-days`, `--cooldown-days` in screener mode (or accept-and-ignore
  with a warning so old commands don't error).

### 6. `src/dashboard/app.py` updates

Add a "Screener Backtest" panel that:
- Lets the user pick universe (sector multi-select or symbol list), date
  range, min_score, narrative on/off.
- Runs the screener mode pipeline and displays:
  - Headline metrics table (horizons × thresholds, hit rates).
  - Histogram of forward returns at the user-selected horizon.
  - Histogram of MFE.
  - Sortable table of all signals with click-through to chart view.
  - Chart view: when a signal is clicked, render OHLCV from
    `signal.date - 60 days` through `signal.date + 252 days`, with
    the signal date marked and forward-return-window shaded. This is
    the visual review interface — the whole point of building this.

This is the highest-value piece for the user's stated workflow ("surface
charts I can look at and decide"). Prioritize the chart-view component.

## Tests

New tests under `tests/`:

### `tests/test_screener.py`
- Synthetic OHLCV where a signal at day 50 is followed by a +30% rally
  over 63 days → assert `forward_returns[63] ≈ 0.30`, MFE ≥ 0.30.
- Signal at day 50, only 30 days of data after → assert
  `forward_returns[63] is None`, `truncated=True`.
- Multi-symbol batch → assert one fetch per symbol (mock the fetcher,
  count calls).

### `tests/test_screener_metrics.py`
- Hand-built outcomes with known returns → assert hit_rates compute
  correctly at each threshold.
- All-truncated outcomes → assert metrics handle gracefully (no division
  by zero).
- Baseline subtraction → assert `excess_returns` = mean − baseline.
- `breakdown_by("pattern")` with mixed patterns → assert per-pattern
  groups are correct.

### `tests/test_refine_screener.py`
- Canned outcomes where Trend Rider has 50% hit rate and Bottom Hunter
  has 10% → assert pattern-gap suggestion fires.
- Outcomes where excess return is -2% → assert `[HIGH]` no-edge
  suggestion fires.
- Outcomes with only 5 signals → assert no suggestions (thin sample gate).

## Implementation order

1. `screener.py` (`measure_forward_returns` + `ForwardOutcome`) + tests.
2. `screener_metrics.py` (`compute_screener_metrics`, `breakdown_by`) + tests.
3. `screener_report.py` (Markdown writer).
4. `refine.py` screener heuristics + tests.
5. `run.py` CLI `--mode` flag wiring.
6. Dashboard panel + chart view.

Each step is a separate commit. Don't move on until tests pass.

## Non-goals (do not do)

- Do not delete `engine.py` or trading metrics. Keep them for diagnostics.
- Do not add stops, trailing exits, or position sizing. There is no trade.
- Do not change `scanner/`, `narrative/`, `features/`, or `research/`.
- Do not add ML or LLM-based outcome classification. Forward returns are
  the ground truth.
- Do not introduce new dependencies. Use pandas, numpy, existing libs.

## Success criteria

After this refactor:
- `python -m src.backtest.run --mode screener --start 2020-01-01` produces
  a markdown report with hit rates, distribution stats, breakdowns, and
  excess-vs-baseline numbers.
- The dashboard exposes a screener panel with a click-to-chart view of any
  historical signal.
- The horrifying -91% drawdown number disappears from default output
  because we no longer simulate trades by default.
- The user can answer the actual question they care about:
  *"Of all the setups this scanner flagged historically, what fraction
  went on to make meaningful moves, and what did the winners look like
  at signal time?"*
