# Stock Finder Agent — Project Notes

A daily stock scanner for high-conviction thematic sectors. Detects two patterns
(**Trend Rider**, **Bottom Hunter**), narrates each match in plain English, and
presents results through a Streamlit dashboard.

## Stack

- **Python 3.12**
- **Streamlit** — dashboard / UI layer
- **pandas + pandas-ta** — OHLCV handling and technical indicators
- **Polygon.io** — primary data source (requires `POLYGON_API_KEY`)
- **yfinance** — automatic fallback when Polygon is unavailable
- **.devcontainer** — reproducible Codespaces setup

## Layout

```
src/
├── config/      sectors universe, scanner thresholds, env loading
├── data/        polygon + yfinance clients, unified fetcher with caching
├── scanner/     indicators, pattern detectors, top-level scan orchestration
├── narrative/   plain-English explanation of each match
├── dashboard/   Streamlit app entrypoint
└── utils/       logging, small shared helpers
```

Run with `streamlit run src/dashboard/app.py` (or `make run`).

## Key design decisions

- **Polygon-first, yfinance-fallback.** The unified fetcher in `src/data/fetcher.py`
  tries Polygon, falls back transparently on auth/HTTP/quota errors. Callers do
  not pick a source.
- **OHLCV cache on disk** at `data/cache/{symbol}_{timeframe}.parquet` keyed by
  symbol + timeframe. Cache is considered fresh for the current trading day.
- **Sectors are data, not code.** `src/config/sectors.py` holds the curated
  thematic universe. Edit this file to retune coverage.
- **Patterns are independent modules.** Each detector in `src/scanner/` takes a
  DataFrame, returns a structured `MatchResult` (or `None`). Adding a new
  pattern = adding one file + registering it in `scanner.run_scan`.
- **Narratives are derived, not stored.** `src/narrative/explain.py` turns a
  match + indicator snapshot into prose. Keep narrative logic out of detectors.
- **Streamlit is a thin view.** All heavy logic lives in `src/scanner/`; the
  dashboard only orchestrates, caches with `@st.cache_data`, and renders.

## Scanner module

The scanner has three layers, each in its own file under `src/scanner/`:

- **`indicators.py`** — single `add_indicators(df)` entry that appends a
  fixed set of columns (SMA20/50/200, EMA21, RSI14, ATR14, vol_sma20, plus
  derived: returns, drawdown_120, SMA slopes, distance-from-MA). Column
  names are exported as constants so detectors never use string literals.
- **`universe.py`** — `build_universe()` composes the discovery list from
  three sources: sector single-stocks (from `config/sectors.py`), SPDR
  sector ETFs, and thematic ETFs (SMH, URA, XBI, ITA, BOTZ, …). Broad
  market (SPY/QQQ/IWM) is opt-in.
- **`patterns/`** — one file per detector, plus `base.py` with
  `MatchResult`, `Factor`, and the small scoring helpers `clamp` /
  `triangular`. Detectors are registered in `patterns/__init__.ALL_DETECTORS`.

### MatchResult & scoring

Every detector returns either `None` or a frozen `MatchResult` with:
`symbol, pattern, score (0-100), as_of, price, sectors, themes, source,
indicators (dict), factors (tuple of Factor)`. `Factor.contribution` is
the points each component added to the score; sum-of-contributions equals
`score`. This is what the narrative layer will read.

Scoring uses `clamp()` and `triangular()` membership functions from
`patterns/base.py` rather than ad-hoc arithmetic — every factor is a
weight × normalized-quality in `[0, 1]`. Component weights per pattern
must sum to 100. Higher = stronger signal; rule of thumb: 70+ clean,
50-70 worth a look, <50 should rarely surface (default `min_score=50`).

### Pattern thresholds

- **Trend Rider** (W: trend 35 / pullback 25 / RSI 25 / slope 15). Hard
  gates: SMA50>SMA200, SMA200 rising, close>SMA200, dist_sma50 in
  [-5%, +10%]. Ideal: RSI(14) ~38, price right at SMA50, SMA50 slope ≥2%.
- **Bottom Hunter** (W: damage 25 / base 25 / curl 25 / RSI 15 / reclaim
  10). Hard gates: 120-bar window dropped ≥15%, ≥30% of window spent
  below SMA200, RSI bottomed below 35 in window, current RSI ≤70.
  Structural legs (damage, base, curl) must all contribute non-zero or
  the match is suppressed.

### Orchestration

`Scanner` (in `scanner.py`) is the public class. Three entry points:
`scan_watchlist(symbols)`, `scan_discovery(...)`, `scan_frame(df, symbol)`
(the last bypasses the fetcher for tests). Results come back as a
`ScanReport` with `matches` (sorted desc by score), `coverage`, `errors`,
and a `to_dataframe()` for table views. The convenience function
`run_scan()` covers the dashboard's common case.

The orchestrator owns the fetch-and-enrich pipeline; detectors never
touch the network and never recompute indicators. A failed fetch on one
symbol logs a warning and is dropped — it never aborts the run.

### Adding a new pattern

1. Add `src/scanner/patterns/<name>.py` defining `NAME` and a
   `detect_<name>(df, symbol) -> MatchResult | None` function.
2. Register it in `patterns/__init__.ALL_DETECTORS`.
3. Add a synthetic series in `tests/synthetic.py` plus a positive and a
   negative test in `tests/test_detectors.py`.

## Narrative layer

Lives in `src/narrative/`. Public entry: `NarrativeScorer.score(ticker)
-> NarrativeResult`. The scorer is optional — passing one to `Scanner`
enriches every match with a narrative read and a composite score;
omitting it leaves the scanner pattern-only and unchanged in behavior.

### Sources

Each source is a small adapter implementing the `NewsSource` protocol
(`name`, `fetch(symbol, limit) -> list[NewsItem]`). Two ship by default:

- **`PolygonNewsSource`** — `/v2/reference/news`. Surfaces Polygon's
  per-ticker `insights[].sentiment` as `NewsItem.external_sentiment`.
  This label beats word counts and is preferred by the sentiment engine
  when present.
- **`YFinanceNewsSource`** — `yf.Ticker(sym).news`. Handles both the
  modern (`content.{title,summary,pubDate,…}`) and legacy flat shapes
  defensively so an upstream layout change degrades gracefully.

Both sources return `[]` on any failure (auth, quota, parse) and log a
warning. A failing news source must never abort a scan.

`default_sources()` returns `[Polygon, yfinance]` — order matters
because dedup keeps the first occurrence, so the higher-quality feed
wins on conflicts.

News is cached to `data/cache/news/{SYMBOL}_{YYYY-MM-DD}.json` and
considered fresh for the rest of the UTC day — same contract as the
OHLCV cache.

### Sentiment

`Sentiment` is a protocol with a single `score_item(NewsItem) -> float`
in `[-1, +1]`. The default `LexiconSentiment` uses the bundled
finance-flavored lexicon in `src/narrative/lexicon.py`: positive vs.
negative word + phrase counts → `(pos - neg) / max(pos + neg, 1)`.
Articles with no lexicon hit score 0 (no signal, not "neutral").

`LexiconSentiment(prefer_external=True)` (the default) returns the
upstream `external_sentiment` when present, falling back to the lexicon
otherwise. To swap in an LLM sentiment engine: implement the protocol
and pass it as `NarrativeScorer(sentiment=MyLLM())` — no other call sites
change.

### Score → 0-1 mapping

For each item the scorer:

1. Calls `sentiment.score_item(item)` → polarity in `[-1, +1]`.
2. Weights items by recency on a `0.5 ** (age_days / half_life)` curve
   (default half-life 5 days). Older items contribute proportionally
   less; items with zero sentiment still count in the denominator so a
   flood of neutral coverage drags the aggregate toward zero.
3. Aggregates into a weighted-average polarity, then maps to `[0, 1]`
   with `0.5 + 0.5 * polarity` (0.5 = neutral / no signal).
4. Damps thin coverage: ≤2 items → score pulled half-way toward 0.5.
5. Builds an explanation: `"{N} recent articles; tone bullish (2+/0-/1~);
   top: 'headline' (Publisher, 2d ago)."`

### Composite blending

`blend_composite(pattern_score, narrative, narrative_weight=0.2)` in
`src/narrative/scorer.py` is the canonical formula:

    composite = (1 - w) * pattern_score + w * (narrative.score * 100)

Defaults: 80% pattern, 20% narrative (`DEFAULT_NARRATIVE_WEIGHT`).
Override per scan via `Scanner(narrative_weight=...)`. The default means
neutral news (0.5) costs a strong pattern ~10 points relative to
pattern-only — small enough that pattern dominates, large enough that
clearly bullish or bearish news re-ranks similar-quality patterns.

### Integration points

- `MatchResult` gained two nullable fields: `narrative: NarrativeResult
  | None` and `composite_score: float | None`. The `effective_score`
  property returns composite when present, pattern score otherwise —
  ranking code uses this property and stays agnostic about whether
  narrative was enabled.
- `Scanner(narrative_scorer=..., narrative_weight=...)` wires the layer.
  Narrative scoring runs only on symbols that produced at least one
  pattern hit (one news fetch per unique symbol, not per match) — much
  cheaper than scoring the whole universe.
- A narrative scoring failure on one symbol logs a warning and leaves
  that match's pattern-only composite intact; it never aborts the scan.
- `run_scan(with_narrative=True)` is the dashboard's one-call form.

### Extensibility

- **New source**: implement `NewsSource`, add to `NarrativeScorer(sources=
  [...])` (or `default_sources()` to make it the global default).
- **LLM sentiment**: implement `Sentiment`, pass via
  `NarrativeScorer(sentiment=...)`. Polygon's per-article insights are
  already an "external" pass-through example.
- **Different blend**: pass `narrative_weight` to `Scanner` /
  `run_scan`, or use `blend_composite()` directly with custom weights.

## Conventions

- Type hints everywhere; dataclasses for structured returns.
- No hard-coded API keys — read from `.env` via `src/config/settings.py`.
- Avoid network I/O at import time.
- Tests under `tests/` (synthetic data, no network).
- Indicator column names are constants in `indicators.py`, not literals.
- Detectors are pure functions of an indicator-augmented DataFrame.
