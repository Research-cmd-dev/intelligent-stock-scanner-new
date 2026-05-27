# Phase 4: Narrative Events Table

## Mission

Build the structured storage layer that turns transcript chunks (and
eventually news articles, X posts, etc.) into queryable `(symbol, theme,
polarity, published_utc, ...)` events. This is the join point between
the rich transcript corpus and the scanner — without it, transcripts
flow into Telegram briefings but cannot reach `BacktestSignal.narrative_score`.

**Scope:** This phase builds the **table, schema, and read/write API only.**
No transcript writers (that's Phase 5). No backtest wiring (that's Phase 6).
No migration of existing Polygon/X/yfinance sources (separate later work).
The deliverable is an empty, well-tested narrative-events store that
Phase 5 will fill and Phase 6 will read from.

**Why this scope:** Smallest valuable unit. We validate the schema and
query API in isolation before any source writes to it. If Phase 4 is
right, Phases 5 and 6 are mechanical.

## Required reading before implementing

Have Claude Code read these before writing any code:

1. `src/narrative/sources/__init__.py` and the existing `NewsSource`
   protocol — to understand the current source contract.
2. `src/narrative/scorer.py` (or wherever `NarrativeScorer` lives) — to
   understand how items are currently aggregated into a 0..1 score.
3. `src/narrative/themes.py` — to understand the current theme structure
   and what `defined_at` will plug into.
4. `src/narrative/sources/youtube/` — to see how the transcript store
   stores chunks, since events will reference these.
5. `PROJECT.md` and `CLAUDE.md` — for project conventions
   (UTC dates, atomic writes, no global state, etc.).

If anything in this spec conflicts with what's in the codebase, the
codebase wins. Ask before deviating.

## Storage location and lifecycle

- **Path on Modal volume:** `/data/narrative/events.db`
- **Path locally (dev):** `data/narrative/events.db`
- **Gitignore:** Confirm `data/narrative/` is already in `.gitignore`
  (it should be, from the transcript work).
- **SQLite, single file, no FTS5 or sqlite-vec needed.** This DB is for
  fast columnar queries by `(symbol, published_utc)`, not full-text
  search. The transcript store at `/data/narrative/transcripts.db`
  remains separate; that's where the FTS lives.
- **Schema version tracked in a `_meta` table.** Any schema change bumps
  the version and runs a migration on open.

## Step 1: `NarrativeEvent` dataclass + SQL schema

Create `src/narrative/events/schema.py`.

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class NarrativeEvent:
    """One structured narrative claim about one ticker at one moment in time.

    Source-indifferent: a transcript chunk emits one row per (chunk, ticker);
    a news article emits one row per (article, ticker); an X post emits one
    row per (post, ticker). The scorer consumes these uniformly.

    Immutable once written. Re-scoring with a new model writes new rows with
    a different model_version — backtests pick the version that would have
    existed at their as_of timestamp.
    """
    id: str                          # sha256(source|provider_id|chunk_id|symbol)[:32]
    symbol: str                      # uppercased ticker
    theme: str | None                # theme name from themes.py; None if no theme matched
    polarity: float                  # [-1.0, +1.0]
    published_utc: datetime          # the only as-of anchor
    source: str                      # "polygon" | "x" | "yfinance" | "youtube_transcript" | "podcast_transcript"
    provider_id: str                 # e.g. polygon article id, youtube video id, podcast guid
    chunk_id: str | None             # for transcript sources; None for article-level sources
    speaker: str | None              # for transcripts; None for articles
    authority_weight: float          # 1.0 default; >1 for tier-1 speakers; <1 for lower-tier or noisy sources
    ticker_match_confidence: float   # [0, 1] — how confident the ticker matcher is
    text_excerpt: str                # short excerpt for audit/dashboard display, NOT for re-scoring
    ingested_utc: datetime           # when this row was written (audit trail, never used for as-of)
    model_version: str               # sentiment model version (e.g. "polarity-v1")
    themes_hash: str                 # sha256(themes.py contents) at write time
    authority_hash: str              # sha256(speaker_authority.py contents) at write time

    def to_row(self) -> tuple: ...
    @classmethod
    def from_row(cls, row: tuple) -> "NarrativeEvent": ...
```

SQL schema (lives in `src/narrative/events/schema.py` as a module-level
constant, or as a `migrations/v001.sql` file — either is fine):

```sql
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    theme TEXT,
    polarity REAL NOT NULL,
    published_utc TEXT NOT NULL,         -- ISO-8601 UTC string
    source TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    chunk_id TEXT,
    speaker TEXT,
    authority_weight REAL NOT NULL DEFAULT 1.0,
    ticker_match_confidence REAL NOT NULL DEFAULT 1.0,
    text_excerpt TEXT NOT NULL,
    ingested_utc TEXT NOT NULL,
    model_version TEXT NOT NULL,
    themes_hash TEXT NOT NULL,
    authority_hash TEXT NOT NULL
);

-- The dominant query: "what does the system know about SYMBOL as of T?"
CREATE INDEX IF NOT EXISTS idx_events_symbol_time
    ON events(symbol, published_utc);

-- Cross-ticker theme queries: "what tickers light up for AI Infrastructure this week?"
CREATE INDEX IF NOT EXISTS idx_events_theme_time
    ON events(theme, published_utc) WHERE theme IS NOT NULL;

-- Source mix audits
CREATE INDEX IF NOT EXISTS idx_events_source_time
    ON events(source, published_utc);

-- For point-in-time queries that span the full universe
CREATE INDEX IF NOT EXISTS idx_events_time
    ON events(published_utc);

CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Insert schema_version = "1" on first open.
```

**Deterministic ID rule:**
```
id = sha256(f"{source}|{provider_id}|{chunk_id or ''}|{symbol}").hexdigest()[:32]
```
This makes inserts idempotent — re-running ingestion never duplicates
events, and an event from a Polygon article about NVDA has a different
ID than an event from a transcript chunk that mentions NVDA, even if
both are about the same news.

Tests for Step 1 (`tests/test_narrative_events_schema.py`):
- Construct a `NarrativeEvent`, call `to_row()`, pass to `from_row()`,
  assert round-trip equality.
- Construct two events with identical (source, provider_id, chunk_id,
  symbol); assert their IDs match.
- Construct two events differing only in symbol; assert IDs differ.
- Construct an event with `polarity=1.5`; assert validation error (or
  that the value is clamped — decide one and document it).

## Step 2: Store (reader + writer)

Create `src/narrative/events/store.py`.

```python
class NarrativeEventStore:
    """SQLite-backed store for NarrativeEvent records.

    Connection is per-instance; the store is not thread-safe by itself.
    Callers are responsible for not sharing a single instance across
    threads. (Modal functions are single-threaded by default, so this
    is fine.)
    """

    def __init__(self, db_path: str | Path):
        """Open the DB, run migrations if needed, set up indexes."""
        ...

    def write_events(self, events: Iterable[NarrativeEvent]) -> int:
        """Batch insert. Uses INSERT OR IGNORE so duplicates are silent
        no-ops. Returns the count of NEW rows actually inserted (not the
        count of input rows).

        Wrap in a single transaction for performance — Phase 5 will call
        this with batches of ~50-200 events per chunk."""
        ...

    def read_events(
        self,
        symbol: str,
        as_of: datetime,
        *,
        lookback_days: int = 14,
        themes_defined_before: datetime | None = None,
    ) -> list[NarrativeEvent]:
        """Return events for `symbol` published in
        [as_of - lookback_days, as_of], oldest first.

        If `themes_defined_before` is set, filter out events whose theme
        was not yet defined as of that timestamp. (Phase 6 will set this
        to as_of; Phase 4 just passes it through and tests it.)

        as_of must be timezone-aware UTC. Lookback is calendar days, not
        trading days. lookback_days=14 is the default because the
        existing NarrativeScorer uses ~5-day half-life recency decay; 14
        days is ~3 half-lives, which is enough."""
        ...

    def read_events_by_theme(
        self,
        theme: str,
        as_of: datetime,
        *,
        lookback_days: int = 7,
    ) -> list[NarrativeEvent]:
        """Cross-ticker theme query for the Universe Narrative dashboard
        tab. Returns events for `theme` in the time window, ordered by
        published_utc desc."""
        ...

    def count_events(self) -> dict[str, int]:
        """Diagnostic: returns {"total": N, "by_source": {...},
        "by_theme": {...}, "earliest_utc": ..., "latest_utc": ...}.
        Used by the dashboard health panel and ad-hoc CLI checks."""
        ...

    def close(self) -> None:
        ...
```

**Critical correctness rules:**
- All timestamps stored as ISO-8601 UTC strings (`YYYY-MM-DDTHH:MM:SSZ`).
  Parsing on read uses `datetime.fromisoformat`. **No naive datetimes
  anywhere.**
- `published_utc` is the only as-of anchor. `ingested_utc` is for audit
  only and must never be used to filter for backtests.
- `write_events` must be atomic per call — either all events in the
  batch are written or none are. Use a single explicit transaction.
- `read_events` must never return events with `published_utc > as_of`.
  This is the point-in-time correctness guarantee. Write a test that
  hammers this edge case.

Tests for Step 2 (`tests/test_narrative_events_store.py`):
- Open a store in a tempfile, write 10 events, read them back, assert
  count and contents match.
- Write the same 10 events twice; assert second write returns 0 new
  inserts (idempotency).
- Write events at times T-30d, T-7d, T-1d, T+1d, T+10d. Read with
  `as_of=T, lookback_days=14`. Assert only the T-7d and T-1d events
  return.
- Same as above but with `lookback_days=60`. Assert T-30d included.
- Cross-ticker theme query: write events on AI Infrastructure for
  NVDA, AMD, ARM. `read_events_by_theme("AI Infrastructure", as_of=T)`
  returns all three.
- Schema migration: open a store on a tempfile, close it, re-open with
  the same path; assert no errors and no duplicate index creation.

## Step 3: Theme metadata + `defined_at`

Extend `src/narrative/themes.py` (or wherever themes are defined) so each
theme has a `defined_at` UTC date. This is the honesty filter for
historical backtests.

If themes are currently defined as plain dicts or constants, the change
is additive — wrap each theme in a small dataclass or add the field to
the existing structure. Don't restructure the whole module; minimum
viable change.

```python
@dataclass(frozen=True)
class ThemeDef:
    name: str
    core_terms: tuple[str, ...]
    support_terms: tuple[str, ...]
    defined_at: date         # UTC date this theme was first added to the system
    # ... existing fields preserved
```

**Seeding `defined_at` for existing themes:** Use the date the theme
first appeared in git history. Have Claude Code run
`git log --diff-filter=A --follow -- src/narrative/themes.py | head -5`
to find candidates, or just use the file's earliest mtime as a
conservative default ("all current themes defined at or before this
date"). Don't agonize over precision — we just need it to be honest, and
"the date the file was first written" is a fine lower bound.

**Add a helper:**
```python
def themes_defined_before(when: datetime) -> set[str]:
    """Return the names of themes whose defined_at <= when. Used by
    NarrativeEventStore.read_events for the honesty filter."""
```

Tests for Step 3 (`tests/test_themes_defined_at.py`):
- Every theme in themes.py has a `defined_at` field of type `date`.
- `themes_defined_before(date.today())` returns all current themes.
- `themes_defined_before(date(2020, 1, 1))` returns an empty set (or
  whatever the earliest defined_at is; assert it's at most a small subset).

## Step 4: Integration test

Create `tests/test_narrative_events_integration.py`. Exercises the full
write → read → filter path end to end.

Scenario: Write 50 synthetic events across 5 symbols and 3 themes,
spanning 30 days. Read with various `as_of`, `lookback_days`, and
`themes_defined_before` parameters. Assert the right subset comes back
each time. This is the regression-prevention test for Phases 5 and 6.

Specifically include:
- Point-in-time correctness: no event with `published_utc > as_of`
  ever returns
- Theme honesty: an event tagged with a theme not yet defined as of
  `themes_defined_before` is filtered out (its `theme` field is treated
  as null in the result, OR the row is dropped entirely — pick one and
  document it; I'd lean toward "theme set to None" so the event is
  still visible but doesn't contribute to that theme's score)
- Idempotency: re-running the write half does not change the read half
- Symbol case-insensitivity: querying for "nvda" returns events written
  for "NVDA" (or document that the caller must uppercase first; I'd
  lean toward enforcing uppercase at write time)

## Implementation order

Strict sequence. No interleaving:

1. `schema.py` (`NarrativeEvent` + SQL schema) + Step 1 tests
2. `store.py` (reader + writer) + Step 2 tests
3. `themes.py` (`defined_at` + helper) + Step 3 tests
4. Integration test (Step 4)

After each step, run that step's tests AND all prior steps' tests.
Don't move on until everything passes.

## Commit strategy

One commit per step, on a feature branch `feat/phase-4-narrative-events`:

- `feat(narrative): add NarrativeEvent dataclass and events schema (phase 4 step 1)`
- `feat(narrative): add NarrativeEventStore reader/writer (phase 4 step 2)`
- `feat(narrative): add defined_at to themes for point-in-time honesty (phase 4 step 3)`
- `test(narrative): end-to-end integration test for events store (phase 4 step 4)`

Push the branch after step 4 passes. Open a PR to main with a summary
of what's in scope and what's explicitly deferred (Phase 5/6). Don't
merge until reviewed.

## Non-goals

- Do **not** implement transcript event emission. That's Phase 5.
- Do **not** wire `NarrativeEventStore` into `NarrativeScorer` or
  `BacktestSignal`. That's Phase 6.
- Do **not** migrate Polygon/X/yfinance sources to write to the events
  table. Separate later PR.
- Do **not** add LLM-based ticker matching or theme detection. Those
  are Phase 5 concerns.
- Do **not** add FTS5 or vector search. The transcript store has those;
  this store doesn't need them.
- Do **not** delete or refactor the existing `NarrativeScorer`. It
  keeps working unchanged until Phase 6 chooses to consume from events.

## Success criteria

After Phase 4 is merged:

1. A new `src/narrative/events/` module exists with `schema.py`,
   `store.py`, and `__init__.py` exposing `NarrativeEvent` and
   `NarrativeEventStore`.
2. Running `pytest tests/test_narrative_events*.py -v` passes all tests.
3. A throwaway script can: open the store at a temp path, write 100
   synthetic events, read them back at a chosen `as_of`, and get the
   right subset — no errors, point-in-time correct.
4. `src/narrative/themes.py` has `defined_at` on every theme and a
   `themes_defined_before(when)` helper.
5. The existing Polygon/X/yfinance → `NarrativeScorer` path is unchanged
   and all pre-existing tests still pass.
6. Nothing writes to `events.db` yet. The table is empty by design.
   Phase 5 fills it.

## What this unblocks

Phase 5 (transcript event emission) writes to this store. Phase 6
(backtest wiring) reads from it via `read_events(symbol,
as_of=signal.published_utc)`. Once both land, every `BacktestSignal`
in a historical screener run will have a point-in-time-correct
`narrative_score`, and we can finally answer: **does narrative
actually improve entry quality?**
