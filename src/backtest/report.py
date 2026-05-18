"""Rendering: backtest report markdown + append-only suggestions log.

Two artifacts per run:

- ``logs/backtest_{YYYYMMDD_HHMMSS}.md`` — full snapshot. Self-contained
  so a reviewer can read one file and see the whole run.
- ``logs/suggestions.md`` — durable, append-only journal of every
  refinement candidate. Each entry is dated and links back to the
  source report. This is the human-review surface; the dashboard's job
  is to surface the most recent block.

Both files live under :func:`src.config.get_settings().logs_dir` if
that's configured, else under the repo's ``logs/`` directory.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.config import get_settings
from src.utils import get_logger

from .engine import BacktestReport
from .metrics import Metrics
from .refine import Suggestion, suggest_improvements

log = get_logger(__name__)


def write_report(
    report: BacktestReport,
    *,
    logs_dir: Path | None = None,
    timestamp: datetime | None = None,
) -> tuple[Path, list[Suggestion]]:
    """Render ``report`` to disk and append any suggestions to the journal.

    Returns the path to the per-run markdown file and the list of
    suggestions emitted, so callers (CLI, dashboard) can echo a summary
    without re-reading from disk.
    """
    ts = timestamp or datetime.utcnow()
    out_dir = _resolve_logs_dir(logs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    suggestions = suggest_improvements(report)

    run_path = out_dir / f"backtest_{ts.strftime('%Y%m%d_%H%M%S')}.md"
    run_path.write_text(_render_run_report(report, suggestions, ts), encoding="utf-8")
    log.info("wrote backtest report: %s", run_path)

    if suggestions:
        _append_suggestions(out_dir, suggestions, ts, run_path)

    return run_path, suggestions


# ---------------------------------------------------------------------- #
# Run report                                                             #
# ---------------------------------------------------------------------- #


def _render_run_report(
    report: BacktestReport,
    suggestions: list[Suggestion],
    ts: datetime,
) -> str:
    lines: list[str] = []
    lines.append(f"# Backtest report — {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append("")

    lines.append("## Parameters")
    lines.append("")
    lines.append(_kv_table(report.params))
    lines.append("")

    lines.append("## Overall metrics")
    lines.append("")
    lines.append(_kv_table(report.metrics.to_row()))
    lines.append("")

    if report.qlib_metrics:
        lines.append("### Qlib risk_analysis (annualized)")
        lines.append("")
        lines.append(_kv_table({k: round(v, 4) for k, v in report.qlib_metrics.items()}))
        lines.append("")
    else:
        lines.append("_Qlib not installed; in-house metrics only. "
                     "`pip install pyqlib` to enable annualized risk_analysis._")
        lines.append("")

    if report.by_pattern:
        lines.append("## By pattern")
        lines.append("")
        lines.append(_metrics_table(report.by_pattern))
        lines.append("")

    if report.by_score_band:
        lines.append("## By score band")
        lines.append("")
        lines.append(_metrics_table(report.by_score_band))
        lines.append("")

    if report.by_sector:
        lines.append("## By sector")
        lines.append("")
        lines.append(_metrics_table(report.by_sector))
        lines.append("")

    lines.append("## Suggestions")
    lines.append("")
    if not suggestions:
        lines.append("_No refinement suggestions for this run._")
    else:
        for s in suggestions:
            lines.append(s.to_markdown())
    lines.append("")

    return "\n".join(lines)


def _kv_table(d: dict[str, object]) -> str:
    if not d:
        return "_(empty)_"
    rows = ["| Metric | Value |", "|---|---|"]
    for k, v in d.items():
        rows.append(f"| {k} | {v} |")
    return "\n".join(rows)


def _metrics_table(buckets: dict[str, Metrics]) -> str:
    if not buckets:
        return "_(empty)_"
    headers = [
        "Bucket", "Trades", "Win rate", "Mean ret %", "Median ret %",
        "Profit factor", "Max DD %", "Best %", "Worst %",
    ]
    rows = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    # Sort buckets so the most-traded appears first — usually the most
    # statistically meaningful read.
    ordered = sorted(buckets.items(), key=lambda kv: kv[1].trade_count, reverse=True)
    for name, m in ordered:
        rows.append("| " + " | ".join([
            name,
            str(m.trade_count),
            f"{m.win_rate:.0%}",
            f"{m.mean_return * 100:.2f}",
            f"{m.median_return * 100:.2f}",
            f"{m.profit_factor:.2f}" if m.profit_factor != float("inf") else "∞",
            f"{m.max_drawdown * 100:.2f}",
            f"{m.best_trade * 100:.2f}",
            f"{m.worst_trade * 100:.2f}",
        ]) + " |")
    return "\n".join(rows)


# ---------------------------------------------------------------------- #
# Suggestions journal                                                    #
# ---------------------------------------------------------------------- #


def _append_suggestions(
    out_dir: Path,
    suggestions: list[Suggestion],
    ts: datetime,
    run_path: Path,
) -> None:
    """Append a dated block to the running suggestions log."""
    log_path = out_dir / "suggestions.md"
    header_new_file = not log_path.exists()

    block: list[str] = []
    if header_new_file:
        block.append("# Backtest suggestions log\n")
        block.append("Append-only journal of refinement candidates. Each block "
                     "links to the source backtest report.\n")
    block.append("")
    block.append(f"## {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    block.append(f"_Source: [{run_path.name}]({run_path.name})_")
    block.append("")
    for s in suggestions:
        block.append(s.to_markdown())
    block.append("")

    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(block))
    log.info("appended %d suggestion(s) to %s", len(suggestions), log_path)


# ---------------------------------------------------------------------- #
# Path resolution                                                        #
# ---------------------------------------------------------------------- #


def _resolve_logs_dir(override: Path | None) -> Path:
    if override is not None:
        return Path(override)
    settings = get_settings()
    # The Settings dataclass exposes log_dir / logs_dir under varying names
    # in different revisions; fall back to the repo logs/ if neither hits.
    for attr in ("logs_dir", "log_dir"):
        candidate = getattr(settings, attr, None)
        if candidate is not None:
            return Path(candidate)
    return Path("logs")
