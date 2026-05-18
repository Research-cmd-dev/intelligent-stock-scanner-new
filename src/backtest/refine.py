"""Self-refinement: turn a :class:`BacktestReport` into improvement suggestions.

Every suggestion is a deterministic read of the breakdowns the metrics
layer already produced — no LLM, no ML. The intent is to surface "the
data clearly shows X; consider trying Y" candidates that a human can
sanity-check and act on, not to auto-tune the strategy.

Heuristics intentionally err on the side of staying quiet: each
threshold is calibrated so that a flat / inconclusive backtest produces
*no* suggestions. We'd rather miss a marginal insight than spam the
suggestions log with noise.

The output flows two places:

- :func:`suggest_improvements` returns them for programmatic use.
- :mod:`src.backtest.report` appends them to ``logs/suggestions.md`` so
  the human review trail is durable across runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .engine import BacktestReport

Priority = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Suggestion:
    """One actionable observation, framed for human review.

    ``category`` is a coarse bucket so the suggestions log can be
    skimmed. ``evidence`` is a plain dict of numeric backing — surfaced
    in the rendered report so the reviewer doesn't have to re-derive
    where the suggestion came from.
    """

    category: str            # "pattern" | "score_threshold" | "sector" | "narrative" | "coverage"
    priority: Priority
    title: str
    rationale: str
    evidence: dict[str, object] = field(default_factory=dict)

    def to_markdown(self) -> str:
        ev = ", ".join(f"{k}={v}" for k, v in self.evidence.items())
        ev_line = f"\n  - _Evidence:_ {ev}" if ev else ""
        return (
            f"- **[{self.priority.upper()}] {self.category}** — {self.title}\n"
            f"  - {self.rationale}{ev_line}"
        )


# ---------------------------------------------------------------------- #
# Tunable thresholds — kept here so they're easy to audit.               #
# ---------------------------------------------------------------------- #


# A breakdown bucket needs at least this many trades before its win
# rate or mean return is treated as load-bearing. Below this we don't
# trust the sample.
MIN_BUCKET_TRADES = 8

# How much one bucket has to beat another (in win-rate percentage
# points, or in mean return basis points) before we'll flag the gap.
PATTERN_GAP_WIN_RATE_PP = 15.0
SECTOR_GAP_WIN_RATE_PP = 20.0
SCORE_BAND_RETURN_BPS = 100.0    # 1 percentage point of mean return

# Backtest-wide health checks.
MIN_TOTAL_TRADES_FOR_CONFIDENCE = 20

# Feature evaluation thresholds. An IR magnitude above this is a clear
# signal worth promoting; below the lower bound is a candidate for
# deprecation if we're using it.
STRONG_IR = 1.0
WEAK_IR = 0.3
FEATURE_MIN_PERIODS = 20


# ---------------------------------------------------------------------- #
# Public entry                                                           #
# ---------------------------------------------------------------------- #


def suggest_improvements(report: "BacktestReport") -> list[Suggestion]:
    """Run every heuristic and return suggestions sorted by priority."""
    suggestions: list[Suggestion] = []

    suggestions.extend(_coverage_checks(report))

    if report.metrics.trade_count >= MIN_TOTAL_TRADES_FOR_CONFIDENCE:
        suggestions.extend(_pattern_gap_check(report))
        suggestions.extend(_score_band_check(report))
        suggestions.extend(_sector_bias_check(report))
        suggestions.extend(_overall_health_check(report))

    if report.features_evaluation is not None:
        suggestions.extend(_feature_ic_checks(report))

    return _rank(suggestions)


# ---------------------------------------------------------------------- #
# Individual heuristics                                                  #
# ---------------------------------------------------------------------- #


def _coverage_checks(report: "BacktestReport") -> list[Suggestion]:
    """Flag scans that didn't produce enough data to draw conclusions."""
    out: list[Suggestion] = []
    n = report.metrics.trade_count
    if n == 0:
        out.append(Suggestion(
            category="coverage",
            priority="high",
            title="Backtest produced zero trades.",
            rationale=(
                "Either no signals fired in the window or every signal failed "
                "to enter (e.g. on the last available bar). Widen the date "
                "range or lower the min_score threshold."
            ),
            evidence={"signals": len(report.signals), "trades": n},
        ))
    elif n < MIN_TOTAL_TRADES_FOR_CONFIDENCE:
        out.append(Suggestion(
            category="coverage",
            priority="medium",
            title=f"Sample size is thin ({n} trades).",
            rationale=(
                "Refinement heuristics are gated until at least "
                f"{MIN_TOTAL_TRADES_FOR_CONFIDENCE} trades exist. Extend the "
                "backtest window or expand the symbol set before reading "
                "much into the metrics."
            ),
            evidence={"trades": n},
        ))
    return out


def _pattern_gap_check(report: "BacktestReport") -> list[Suggestion]:
    """If one pattern's win rate trounces the other, flag the laggard."""
    eligible = {
        name: m for name, m in report.by_pattern.items()
        if m.trade_count >= MIN_BUCKET_TRADES
    }
    if len(eligible) < 2:
        return []

    ranked = sorted(eligible.items(), key=lambda kv: kv[1].win_rate, reverse=True)
    leader_name, leader = ranked[0]
    laggard_name, laggard = ranked[-1]
    gap_pp = (leader.win_rate - laggard.win_rate) * 100
    if gap_pp < PATTERN_GAP_WIN_RATE_PP:
        return []

    return [Suggestion(
        category="pattern",
        priority="high" if gap_pp >= 25 else "medium",
        title=f"{laggard_name} underperforms {leader_name} by {gap_pp:.1f}pp win rate.",
        rationale=(
            f"Across {laggard.trade_count} {laggard_name} trades the win rate is "
            f"{laggard.win_rate:.0%}, vs {leader.win_rate:.0%} for {leader_name} "
            f"({leader.trade_count} trades). Consider tightening "
            f"{laggard_name}'s hard gates or raising its component weights "
            "on the factors that distinguish good setups from marginal ones."
        ),
        evidence={
            f"{leader_name}_win_rate": round(leader.win_rate, 3),
            f"{leader_name}_mean_return_pct": round(leader.mean_return * 100, 2),
            f"{laggard_name}_win_rate": round(laggard.win_rate, 3),
            f"{laggard_name}_mean_return_pct": round(laggard.mean_return * 100, 2),
        },
    )]


def _score_band_check(report: "BacktestReport") -> list[Suggestion]:
    """If high-score signals clearly beat low-score signals, suggest raising the floor."""
    bands = report.by_score_band
    eligible = {
        name: m for name, m in bands.items() if m.trade_count >= MIN_BUCKET_TRADES
    }
    if "50-59" not in eligible:
        return []
    bottom = eligible["50-59"]

    # "Top band" = whichever of 70-79 / 80-100 has data — both means almost
    # certainly the higher one because score 80+ is rare.
    top = None
    for name in ("80-100", "70-79"):
        if name in eligible:
            top = eligible[name]
            break
    if top is None:
        return []

    return_gap_bps = (top.mean_return - bottom.mean_return) * 10_000
    if return_gap_bps < SCORE_BAND_RETURN_BPS:
        return []

    new_floor = 70 if "80-100" in eligible else 60
    return [Suggestion(
        category="score_threshold",
        priority="high" if return_gap_bps >= 250 else "medium",
        title=f"Low-score signals lag high-score by {return_gap_bps/100:.1f}pp mean return.",
        rationale=(
            f"50-59 band: mean return {bottom.mean_return:.1%}, win rate "
            f"{bottom.win_rate:.0%} over {bottom.trade_count} trades. "
            f"High band: mean return {top.mean_return:.1%}, win rate "
            f"{top.win_rate:.0%} over {top.trade_count} trades. "
            f"Consider raising the dashboard's default min_score from "
            f"the current floor toward {new_floor}."
        ),
        evidence={
            "low_band_mean_return_pct": round(bottom.mean_return * 100, 2),
            "low_band_win_rate": round(bottom.win_rate, 3),
            "high_band_mean_return_pct": round(top.mean_return * 100, 2),
            "high_band_win_rate": round(top.win_rate, 3),
            "suggested_min_score": new_floor,
        },
    )]


def _sector_bias_check(report: "BacktestReport") -> list[Suggestion]:
    """Flag sectors that are clear outliers vs. the universe baseline."""
    sectors = {
        name: m for name, m in report.by_sector.items()
        if m.trade_count >= MIN_BUCKET_TRADES and name != "(unclassified)"
    }
    if len(sectors) < 2:
        return []

    baseline = report.metrics.win_rate
    out: list[Suggestion] = []
    for name, m in sectors.items():
        gap_pp = (m.win_rate - baseline) * 100
        if abs(gap_pp) < SECTOR_GAP_WIN_RATE_PP:
            continue
        direction = "outperforms" if gap_pp > 0 else "underperforms"
        out.append(Suggestion(
            category="sector",
            priority="medium",
            title=f"{name} {direction} the baseline by {abs(gap_pp):.1f}pp.",
            rationale=(
                f"{name}: win rate {m.win_rate:.0%}, mean return "
                f"{m.mean_return:.1%} over {m.trade_count} trades. "
                f"Baseline win rate is {baseline:.0%}. Consider whether the "
                f"detectors should be sector-aware or whether the universe "
                f"weighting in src/config/sectors.py should adjust."
            ),
            evidence={
                "sector_win_rate": round(m.win_rate, 3),
                "sector_mean_return_pct": round(m.mean_return * 100, 2),
                "baseline_win_rate": round(baseline, 3),
                "trades": m.trade_count,
            },
        ))
    return out


def _overall_health_check(report: "BacktestReport") -> list[Suggestion]:
    """Catch macro problems the bucket-level checks would miss."""
    out: list[Suggestion] = []
    m = report.metrics
    if m.mean_return < 0:
        out.append(Suggestion(
            category="pattern",
            priority="high",
            title="Mean trade return is negative.",
            rationale=(
                f"Over {m.trade_count} trades the average return is "
                f"{m.mean_return:.1%}. The current rules are losing money "
                "on average. Likely culprits: holding window mismatch, "
                "regime difference between the backtest period and live, "
                "or pattern gates that admit too many marginal setups."
            ),
            evidence={
                "trade_count": m.trade_count,
                "mean_return_pct": round(m.mean_return * 100, 2),
                "win_rate": round(m.win_rate, 3),
                "profit_factor": round(m.profit_factor, 2),
            },
        ))
    elif m.profit_factor < 1.0 and m.trade_count >= MIN_TOTAL_TRADES_FOR_CONFIDENCE:
        out.append(Suggestion(
            category="pattern",
            priority="medium",
            title=f"Profit factor below 1.0 ({m.profit_factor:.2f}).",
            rationale=(
                "Gross losses exceed gross profits even though the win rate "
                "may look acceptable. The losers are bigger than the winners "
                "on average — investigate exits or stop-loss logic."
            ),
            evidence={
                "profit_factor": round(m.profit_factor, 2),
                "win_rate": round(m.win_rate, 3),
                "best_trade_pct": round(m.best_trade * 100, 2),
                "worst_trade_pct": round(m.worst_trade * 100, 2),
            },
        ))
    return out


# ---------------------------------------------------------------------- #
# Ordering                                                               #
# ---------------------------------------------------------------------- #


# Features the current scanner already consumes — surfacing these in
# the IC report is informational; we don't propose "promoting" what we
# already use. Match by case-insensitive substring on the feature name.
_SCANNER_INPUTS = ("rsi14", "dist_sma50", "dist_sma200", "drawdown_120")


def _feature_ic_checks(report: "BacktestReport") -> list[Suggestion]:
    """Surface the strongest features that aren't already in detectors."""
    fe = report.features_evaluation
    if fe is None or not fe.stats:
        return []

    out: list[Suggestion] = []
    promoted = 0
    weak_called_out = 0

    for stat in fe.stats:
        if stat.n_periods < FEATURE_MIN_PERIODS:
            continue
        name_l = stat.name.lower()
        already_used = any(tag in name_l for tag in _SCANNER_INPUTS)

        if stat.abs_ir >= STRONG_IR and promoted < 3 and not already_used:
            direction = "positive" if stat.ir > 0 else "negative"
            out.append(Suggestion(
                category="feature",
                priority="high" if stat.abs_ir >= 1.5 else "medium",
                title=(
                    f"Feature '{stat.name}' has {direction} IR={stat.ir:+.2f} "
                    f"({stat.category}). Consider promoting."
                ),
                rationale=(
                    f"Mean IC {stat.mean_ic:+.3f} over {stat.n_periods} periods, "
                    f"t-stat {stat.t_stat:+.2f}. A {direction} information ratio "
                    "at this magnitude is strong enough to encode as a gate or "
                    f"factor in a new detector — or to weight into the existing "
                    f"{stat.category}-derived scoring."
                ),
                evidence={
                    "feature": stat.name,
                    "category": stat.category,
                    "mean_ic": round(stat.mean_ic, 4),
                    "ir": round(stat.ir, 3),
                    "n_periods": stat.n_periods,
                    "n_obs": stat.n_observations,
                },
            ))
            promoted += 1

        if already_used and stat.abs_ir < WEAK_IR and weak_called_out < 2:
            out.append(Suggestion(
                category="feature",
                priority="low",
                title=(
                    f"Scanner input '{stat.name}' has weak IR={stat.ir:+.2f}; "
                    "review its weight."
                ),
                rationale=(
                    f"Mean IC {stat.mean_ic:+.3f} over {stat.n_periods} periods "
                    "is barely distinguishable from noise. The current detector "
                    "may be over-weighting this factor — consider lowering its "
                    "component weight or moving it from a gate to a soft signal."
                ),
                evidence={
                    "feature": stat.name,
                    "mean_ic": round(stat.mean_ic, 4),
                    "ir": round(stat.ir, 3),
                    "n_periods": stat.n_periods,
                },
            ))
            weak_called_out += 1

    return out


_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _rank(suggestions: list[Suggestion]) -> list[Suggestion]:
    return sorted(
        suggestions,
        key=lambda s: (_PRIORITY_RANK.get(s.priority, 99), s.category, s.title),
    )
