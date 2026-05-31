# Decision Log (append-only — never delete entries)
## Synthetic-fixture E2E over live-data tests
Pipeline is verified hermetically by injecting synthetic OHLCV at the fetch_many
seam. Reason: fast, deterministic, CI-safe, no API keys. Live verification is
manual via the dashboard.
## `make scan` is a known stale no-op
scanner.py has no __main__ guard, so `python -m src.scanner.scanner` exits
silently. Left as-is for now (live runs go through run_scan via the dashboard).
Do not treat as a verify step. Revisit if a real CLI is wanted.
## Rules-based / technical foundation before ML
Transparent, explainable scoring first; technicals are the base signal, narrative
is a multiplier. Reason: conviction must be inspectable.
## Polygon → yfinance fallback
Start usable without a paid key; swap up without touching downstream.
## Modal as opt-in compute
Heavy jobs (full-universe downloads, backtests) run remotely on Modal; the core
runs fine locally without it.
## Coding agent
Project has used Grok Build as a coding agent; now also driven via Claude Code.
## Narrative events are append-only and immutable
Narrative events are append-only and immutable; re-scoring writes new rows keyed
by model_version; published_utc is the sole as-of anchor, ingested_utc is
audit-only. Reason: point-in-time correctness for backtest joins; mutating in
place would inject lookahead bias.
<<< add new decisions here >>>
