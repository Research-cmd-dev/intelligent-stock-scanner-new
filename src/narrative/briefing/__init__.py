"""Phase 3.7 — daily narrative briefing layer.

Public entry point is :func:`briefing.run_briefing`. The package is a
self-contained slice on top of the Phase 3 transcript corpus: it reads
``episodes`` + ``chunks`` from ``transcripts.db``, calls Claude
(Haiku per episode, Sonnet for the daily aggregation), writes one
Markdown briefing per UTC day to the ``stock_data`` Modal volume, and
persists the structured JSON in a ``briefings`` table for novelty
detection on subsequent days.

Modules:
  * :mod:`ticker_aggregator` — pure-math cross-episode ticker rollup
  * :mod:`novelty_detector`  — theme novelty vs. the last N briefings
  * :mod:`markdown_formatter` — JSON briefing → human Markdown
  * :mod:`llm_summarizer`    — per-episode Haiku call
  * :mod:`llm_aggregator`    — daily Sonnet aggregation call
  * :mod:`storage`           — Markdown file I/O to the volume
  * :mod:`briefing`          — orchestrator (``run_briefing``)
"""
