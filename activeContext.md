# Active Context
## Current task
**Phase 4 — Narrative Events Table.** Build the structured storage layer that
turns transcript chunks (later: news, X posts) into queryable
`(symbol, theme, polarity, published_utc, …)` events — the join point between
the transcript corpus and `BacktestSignal.narrative_score`. Spec is
authoritative: `docs/specs/PHASE_4_NARRATIVE_EVENTS.md`. Codebase wins over spec
on any conflict; ask before deviating.

**Scope this phase:** table + schema + read/write API only. NOT in scope:
transcript writers (Phase 5), backtest wiring (Phase 6), migrating
Polygon/X/yfinance sources. Deliverable = an empty, well-tested events store.

## State
- Branch: `feat/phase-4-narrative-events`. Working tree clean. HEAD `e92ee6b`.
- Store path: `data/narrative/events.db` (local) / `/data/narrative/events.db`
  (Modal). SQLite, single file, no FTS5. Schema version tracked in a `_meta`
  table; transcript store at `transcripts.db` stays separate.

## Step checklist (tick as completed; run that step's tests + all prior)
- [x] **Step 1** — `NarrativeEvent` dataclass + `EVENTS_SCHEMA_SQL` in
      `src/narrative/events/schema.py` + `tests/test_narrative_events_schema.py`.
      Committed `e92ee6b`.
- [ ] **Step 2** — `NarrativeEventStore` reader/writer in
      `src/narrative/events/store.py` + `tests/test_narrative_events_store.py`. ← **next**
- [ ] **Step 3** — `defined_at` on themes (`src/narrative/themes.py`) for
      point-in-time honesty + `tests/test_themes_defined_at.py`.
- [ ] **Step 4** — end-to-end integration test.
- [ ] Push branch + open PR to `main` (one commit per step, per spec's commit strategy).

## Watch-outs
- Events are append-only and immutable; re-scoring writes new rows with a new
  `model_version`. `published_utc` is the sole as-of anchor; `ingested_utc` is
  audit-only. Mutating in place would inject lookahead bias into backtest joins.
- All UTC dates via `src/utils/time.py` helpers. Confirm `data/narrative/` is
  gitignored (it is — `.gitignore` line `data/narrative/`).
- Unrelated carry-forward (Webshare proxy, aggregator mcap, backfill validation)
  lives in NOTES.md / progress.md "Deferred" — do not let it bleed into this task.
