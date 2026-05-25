"""Cross-episode ticker rollup. Pure math, no LLM, no I/O.

Given the per-episode ``tickers`` lists, computes one row per symbol
with total mention count, mention-weighted average sentiment, and a
direction label (``bullish_dominant`` / ``bearish_dominant`` /
``mixed`` / ``neutral``).

The thresholds chosen are deliberately wide; the LLM aggregator gets
the rollup as input and can refine the headline framing from there.
"""

from __future__ import annotations

from typing import Any


# Sentiment thresholds for the direction label.
_BULLISH_CUTOFF = 0.2
_BEARISH_CUTOFF = -0.2


def rollup_tickers(episode_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate ticker mentions across the day's episodes.

    Returns rows sorted by ``total_mentions`` desc, then ``symbol`` asc.
    """
    by_symbol: dict[str, dict[str, Any]] = {}
    for ep in episode_summaries:
        for t in ep.get("tickers") or []:
            symbol = (t.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            mentions = int(t.get("mentions") or 0)
            sentiment = float(t.get("sentiment") or 0.0)
            slot = by_symbol.setdefault(symbol, {
                "symbol": symbol,
                "total_mentions": 0,
                "_weighted_sentiment": 0.0,
                "episode_ids": set(),
            })
            slot["total_mentions"] += mentions
            slot["_weighted_sentiment"] += sentiment * mentions
            ep_id = ep.get("episode_id")
            if ep_id:
                slot["episode_ids"].add(ep_id)

    out: list[dict[str, Any]] = []
    for slot in by_symbol.values():
        total = slot["total_mentions"]
        avg = (slot["_weighted_sentiment"] / total) if total > 0 else 0.0
        out.append({
            "symbol": slot["symbol"],
            "total_mentions": total,
            "avg_sentiment": round(avg, 3),
            "episode_count": len(slot["episode_ids"]),
            "direction": _direction(avg),
        })
    out.sort(key=lambda r: (-r["total_mentions"], r["symbol"]))
    return out


def _direction(avg_sentiment: float) -> str:
    if avg_sentiment >= _BULLISH_CUTOFF:
        return "bullish_dominant"
    if avg_sentiment <= _BEARISH_CUTOFF:
        return "bearish_dominant"
    if -_BULLISH_CUTOFF < avg_sentiment < _BULLISH_CUTOFF:
        # Tight band around zero with no spread → neutral; otherwise mixed.
        return "neutral" if abs(avg_sentiment) < 0.05 else "mixed"
    return "mixed"
