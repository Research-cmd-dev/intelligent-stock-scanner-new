# Project Context
## Mission
Proactively surface emerging, under-appreciated narratives across high-growth
sectors AND technically clean chart setups with asymmetric upside, for
macro-timeframe (not day-trading) idea generation. Quality over quantity:
5–10 high-conviction setups beat 50 mediocre ones.
## Philosophy
Technicals are the foundation; narrative/catalysts are multipliers. Transparent
scoring that shows WHY a setup is high-conviction. Deep research is a secondary
validation layer that fires only on the highest-conviction setups.
## Universe
~560 tickers across 16 sectors: 8 large-cap majors (AI, Chips, Energy, Space,
Robotics, Bio, Software, Misc) + 8 micro lanes (carrying quantum, nuclear,
batteries, fintech, crypto-miner exposure).
## Patterns
- Trend Rider: pullbacks inside established uptrends.
- Bottom Hunter: rounding bottoms reclaiming long-term moving averages.
## Architecture (module layout)
- src/scanner/      : orchestrator (Scanner, run_scan), indicators, universe,
                      patterns/ (trend_rider, bottom_hunter, base detector).
- src/data/         : Polygon client (if POLYGON_API_KEY) else yfinance fallback;
                      historical store; unified fetch_many/fetch_ohlcv.
- src/features/     : alpha158, custom features, pipeline, evaluator.
- src/narrative/    : theme/catalyst/sentiment scoring; sources (Polygon news,
                      yfinance news, X, YouTube); briefing/ (LLM summarize→
                      aggregate→research→Telegram delivery).
- src/research/     : LLMResearcher deep-research layer (anthropic).
- src/backtest/     : engine, signals, screener, metrics, report, run.
- src/modal_app/    : opt-in remote compute (downloads, backtests) + stock_data volume.
- src/dashboard/    : Streamlit app (primary live entry point).
- src/utils/time.py : centralized UTC helpers — all "today" logic MUST use these.
## Data sources
Polygon (keyed) → yfinance (fallback). Parquet cache in data/cache.
## Optional layers (silently skipped if env/keys absent)
Narrative X posts (X_BEARER_TOKEN), deep research (anthropic), Modal compute,
briefing/Telegram delivery, Webshare proxy for YouTube transcripts.
## Non-goals
Not financial advice. Research/education. No live order execution.
