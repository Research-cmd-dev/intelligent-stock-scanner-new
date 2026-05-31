# Progress (append-only — completed work only)
- Phase 3.7 — Briefing pipeline (Haiku per-episode → Sonnet aggregation →
  picks researcher with web_search verification → Telegram delivery).
- 2026-05-27 — Daily briefing cron enabled (04:17 ingest, 05:30 briefing).
- Two-pattern scanner (Trend Rider, Bottom Hunter) over 16-sector universe,
  299 tests passing.
- Narrative layer (themes, catalysts, sentiment, X/news/YouTube sources).
- Modal compute layer (downloads, backtests, stock_data volume).
## In progress
- **Phase 4 — Narrative events table** (branch `feat/phase-4-narrative-events`).
  Structured `(symbol, theme, polarity, published_utc, …)` store that joins the
  transcript corpus to the scanner. Scope: schema + store + read/write API only
  (no source writers, no backtest wiring — those are Phases 5/6). Step 1
  (`NarrativeEvent` dataclass + SQL schema + tests) committed `e92ee6b`.
  Steps 2–4 (store reader/writer, `themes.defined_at`, integration test)
  pending. Spec: `docs/specs/PHASE_4_NARRATIVE_EVENTS.md`.
## Deferred / known issues (from NOTES.md)
- Webshare proxy unconfigured (YouTube candidates silently dropped).
- Aggregator mcap estimates systematically low pre-verification.
- Backfill validation failures (~260 episodes, ~80 with chunks).
<<< append completed items here >>>
