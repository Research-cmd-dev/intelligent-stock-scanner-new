"""Small finance-flavored sentiment lexicon.

Inspired by Loughran-McDonald but trimmed to terms that show up in
*headlines* — the only text many news APIs actually expose without
deeper article scraping.

Words are stored as lowercase, hyphens normalized to spaces. Multi-word
phrases ("all time high") are supported and matched as substrings on
the normalized text. Single words must match a whole token.

Adding new terms: prefer concrete, unambiguous business outcomes (an
earnings beat, a contract win, a probe, a recall) over fuzzy adjectives
("good", "bad") which fire too often on neutral coverage.
"""

from __future__ import annotations

POSITIVE_WORDS: frozenset[str] = frozenset({
    # Earnings / results
    "beat", "beats", "beaten", "exceeded", "exceeds", "outperform",
    "outperformed", "outperforms", "topped", "tops", "smashed",
    # Trend / price action
    "surge", "surged", "surges", "soar", "soared", "soars", "rally",
    "rallied", "rallies", "jumped", "jumps", "leap", "leaped",
    "climbed", "climb", "climbs", "rebound", "rebounded",
    # Business growth
    "growth", "expand", "expands", "expanded", "expansion",
    "accelerate", "accelerated", "accelerates", "acceleration",
    "momentum", "record", "milestone", "breakthrough",
    # Profitability
    "profit", "profitable", "profits", "earnings", "revenue",
    "boost", "boosted", "boosts",
    # Ratings / analyst
    "upgrade", "upgraded", "upgrades", "bullish", "buy", "overweight",
    # Wins
    "win", "wins", "won", "awarded", "awards", "contract", "partnership",
    "approval", "approved", "approves", "cleared", "clears",
    "succeed", "succeeded", "successful",
})

POSITIVE_PHRASES: tuple[str, ...] = (
    "all time high", "all-time high", "record high", "price target raised",
    "better than expected", "ahead of estimates", "guidance raised",
    "fda approval", "wins contract", "exceeds expectations",
    "tops estimates",
    # preserve directional meaning for "raise" even after removing the bare word
    "raised guidance", "raises guidance", "capital raise",
)

NEGATIVE_WORDS: frozenset[str] = frozenset({
    # Earnings / results
    "miss", "missed", "misses", "underperform", "underperformed",
    "underperforms", "disappoint", "disappointed", "disappointing",
    # Trend / price action
    "plunge", "plunged", "plunges", "plummet", "plummeted",
    "drop", "dropped", "drops", "fall", "fell", "falls", "fallen",
    "decline", "declined", "declines", "slump", "slumped", "slumps",
    "tumble", "tumbled", "tumbles", "sink", "sank",
    # Business pain
    "loss", "losses", "weak", "weakness", "weaker", "slowdown",
    "layoff", "layoffs", "restructuring", "writedown", "writeoff",
    "delay", "delayed", "delays", "halt", "halted", "suspend",
    "suspended", "shutdown", "slash", "slashed",
    "warn", "warning", "warned",
    # Legal / regulatory
    "lawsuit", "sued", "probe", "investigation", "investigated",
    "fraud", "recall", "recalled", "subpoena", "fine", "fined",
    "penalty",
    # Ratings
    "downgrade", "downgraded", "downgrades", "bearish", "sell",
    "underweight",
    # Solvency
    "bankruptcy", "bankrupt", "default", "defaulted", "insolvent",
})

NEGATIVE_PHRASES: tuple[str, ...] = (
    "missed estimates", "worse than expected", "below estimates",
    "guidance cut", "cuts guidance", "price target cut", "going concern",
    "files for bankruptcy", "short seller", "accounting irregularities",
    "profit warning",
)
