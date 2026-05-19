# Stock Finder Agent — Project Notes

A daily stock scanner for high-conviction thematic sectors. Detects two patterns
(**Trend Rider**, **Bottom Hunter**), narrates each match in plain English, and
presents results through a Streamlit dashboard.

### Mission & Vision

**Primary Mission**  
To proactively identify emerging and under-appreciated narratives across high-growth sectors — including AI infrastructure, biotechnology, robotics, space, and beyond — and surface technically clean chart setups that offer strong asymmetric upside potential for longer-term investment.

**Core Philosophy**
- Prioritize forward-looking narratives over mainstream ones
- Focus on asymmetric reward-to-risk opportunities
- Maintain a buy-only bias with a longer-term (macro/swing) orientation
- Combine technical pattern confirmation with intelligent narrative analysis
- Serve primarily as a high-quality idea generation engine

**Supporting Capability**  
Deep fundamental research serves as a secondary validation layer that activates only on the highest-conviction setups to evaluate company quality, management track record, partnerships, financial health, and key risks.

**Design Principles**
- The scanner should remain fast and scalable for idea generation
- Narrative intelligence should focus on early-stage and under-followed themes
- The system must be modular and easy to evolve over time
- All major decisions should align with finding asymmetric upside

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
├── narrative/   theme + catalyst detection, sentiment, composite scoring
├── features/    Alpha158-lite + custom features, IC/IR evaluation
├── backtest/    historical replay, metrics, self-refinement heuristics
├── research/    deep-research scaffold (protocol + null baseline) — opt-in
│                secondary validation for the highest-conviction matches
├── dashboard/   Streamlit app entrypoint
└── utils/       logging, small shared helpers
```

The flow follows the mission: scanner + narrative are the fast, primary
**idea-generation** engine; features + backtest close the loop on
historical performance; research is the **opt-in secondary validation**
that runs only against high-conviction matches.

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

### Theme + catalyst detection

Two new modules add structured awareness to the scorer:

- **`themes.py`** — keyword-based detection of recurring storylines:
  *AI Infrastructure*, *Power + Compute* (cheap power paired with
  GPU/AI), *Miner-to-AI Pivot*, *Neocloud / GPU Cloud*, *Data Center
  Build-out*, *Nuclear for AI*, *Sovereign AI*. Each theme has
  `core_terms` (one must match), `support_terms` (each boosts
  relevance), and optional `required_pairs` so composite themes only
  fire when both halves of the story appear ("bitcoin miner" *and*
  "AI", "hydropower" *and* "GPU"). Returns
  `list[ThemeTag(name, relevance ∈ [0,1], matched_terms)]`.
- **`catalysts.py`** — phrase-based detection of discrete events:
  *contract*, *funding*, *expansion*, *pivot*, *m&a*,
  *earnings_beat*, *partnership*, *approval*. Each kind has
  `phrases` and optional `strong_phrases` (anchor customer,
  oversubscribed, gigawatt-scale, …). Returns
  `list[CatalystTag(kind, label, strength, matched_terms)]`.

Detection is intentionally rule-based — no ML — so the catalog is
auditable and easy to retune in one file. To add a theme or catalyst,
drop a `ThemeDef` / `CatalystDef` into the module's `DEFAULT_*` tuple
and add a canned-headline test.

### Score → 0-1 mapping

For each item the scorer:

1. Calls `sentiment.score_item(item)` → polarity in `[-1, +1]`.
2. Tags the item's title + summary with themes + catalysts. The two
   tag sets feed a per-item *conviction boost* in
   `[0, ~0.6]` = `0.30 * theme_strength + 0.30 * catalyst_strength`.
3. Weights items by recency on a `0.5 ** (age_days / half_life)` curve
   (default half-life 5 days), then multiplies that weight by
   `(1 + conviction_boost)`. A bullish article about a recognized
   high-conviction theme *with* a catalyst counts ~1.6x a generic
   bullish article — but a *bearish* article on the same themes
   keeps its negative sign and pulls harder too. Themes / catalysts
   amplify, they never override sentiment direction.
4. Items with zero sentiment still count in the weight denominator so
   a flood of neutral coverage drags the aggregate toward zero.
5. Aggregates into a weighted-average polarity, then maps to `[0, 1]`
   with `0.5 + 0.5 * polarity` (0.5 = neutral / no signal).
6. Damps thin coverage: ≤2 items → score pulled half-way toward 0.5.
7. Aggregates per-item theme + catalyst tags across the basket — a
   theme that shows up across multiple articles strengthens, an
   isolated tag stays modest. Top three of each are attached to
   `NarrativeResult.themes` / `.catalysts`.
8. Builds an explanation that surfaces the strongest signals:
   `"{N} recent articles; tone bullish (2+/0-/1~); themes: Miner-to-AI
   Pivot + Power + Compute; catalyst: customer / contract win; top:
   'headline' (Publisher, 2d ago)."` Themes / catalysts only appear
   when their aggregate score clears 0.4 — keeps the line clean on
   generic coverage.

### Composite blending

`blend_composite(pattern_score, narrative, narrative_weight=0.2)` in
`src/narrative/scorer.py` is the canonical formula:

    composite = (1 - w) * pattern_score + w * (narrative.score * 100)

Defaults: 80% pattern, 20% narrative (`DEFAULT_NARRATIVE_WEIGHT`).
Override per scan via `Scanner(narrative_weight=...)`. The default means
neutral news (0.5) costs a strong pattern ~10 points relative to
pattern-only — small enough that pattern dominates, large enough that
clearly bullish or bearish news re-ranks similar-quality patterns.

### Why this helps with emerging small-cap AI-infra setups

A traditional sentiment-only scorer treats "miner signs hyperscaler
deal for AI compute" and "miner reports quarterly metrics" as
equivalent bullish coverage. The theme + catalyst layer separates
them:

- *Theme* recognition surfaces that the first story is a
  *Miner-to-AI Pivot + Power + Compute + Data Center Build-out* play
  — three composite themes the agent specifically watches for.
- *Catalyst* recognition tags the same article as a `contract` event
  with `anchor customer` strength — a step-change event, not a routine
  update.
- The combined boost pulls the per-item weight ~1.5x, so a thin news
  basket on an emerging name can still produce a clear bullish
  narrative score when the few items that exist are the *right kind*
  of items.
- The explanation surfaces both layers in plain English, so a reader
  can scan a ranked match list and see *why* a small-cap is suddenly
  on the radar — not just that "sentiment is up."

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

## Backtest + self-refinement layer

Lives in `src/backtest/`. Closes the loop between live scanning and
historical performance. One public entry: `run_backtest(symbols, *,
start, end, ...)` returns a `BacktestReport`; `write_report(report)`
renders Markdown to disk.

### Pipeline

1. **`signals.generate_signals`** — replays the scanner bar-by-bar
   across history. For each symbol we fetch the full OHLCV once (cache
   hit), enrich with indicators once, then slice the enriched frame at
   each in-window bar and call `Scanner.scan_frame(slice_, symbol)`.
   This reuses the exact detector code the dashboard runs — no
   second implementation to drift.
2. **`engine.simulate_trades`** — buy next bar's open, hold N trading
   days, sell close. A `(symbol, pattern)` cooldown skips re-entries
   for a configurable number of days. Signals on the last available
   bar (no forward entry) are dropped; signals whose window extends
   past `end` are held to the last available bar and marked
   `truncated=True` so the metrics layer can audit them.
3. **`metrics.compute_metrics`** — win rate, mean / median return,
   profit factor, max drawdown of the cumulative-sum equity curve,
   `sharpe_like = mean/std × √N`, plus `breakdown_by(trades, "pattern"
   | "sector")` and `breakdown_by_score_band(trades)`. Sector
   breakdowns fan multi-sector signals into every group they touch.
4. **`metrics.compute_qlib_metrics`** — `risk_analysis` from
   `qlib.contrib.evaluate` when `qlib` is importable; otherwise
   `None`. Qlib is an *enhancement*, not a hard dep — the system
   produces the same `BacktestReport` either way. `pip install pyqlib`
   to enable annualized risk metrics.
5. **`refine.suggest_improvements`** — deterministic heuristics over
   the breakdowns. No LLM, no ML. Each rule has a coverage gate
   (`MIN_BUCKET_TRADES`, `MIN_TOTAL_TRADES_FOR_CONFIDENCE`) so a thin
   sample produces *no* suggestions rather than noisy ones. Rules:
   pattern win-rate gap, low-vs-high score band performance, sector
   bias against baseline, negative mean return, profit factor < 1.
6. **`report.write_report`** — one Markdown per run at
   `logs/backtest_{YYYYMMDD_HHMMSS}.md` (parameters, overall metrics,
   Qlib block when available, breakdowns, suggestions) plus a dated
   block appended to `logs/suggestions.md` — the append-only journal
   the human reviewer reads.

### CLI

```
python -m src.backtest.run --symbols NVDA,PLTR --start 2024-01-01
python -m src.backtest.run --sector AI Chips --start 2024-01-01
python -m src.backtest.run --start 2024-01-01            # full universe
```

Defaults: `--min-score 60`, `--hold-days 20`, `--cooldown-days 0`. The
CLI prints a one-line summary (signals, trades, win rate, profit
factor, drawdown) plus every suggestion title, and writes both
artifacts under the resolved logs directory.

### Adding a new heuristic

1. Write a `_check_*(report) -> list[Suggestion]` in `refine.py`.
2. Gate it on `MIN_BUCKET_TRADES` for any bucket reads, and on
   `MIN_TOTAL_TRADES_FOR_CONFIDENCE` if it depends on stable aggregates.
3. Register it in `suggest_improvements`.
4. Add a canned-trades test in `tests/test_backtest.py` that proves it
   *fires* in the targeted scenario and *stays quiet* on a clean run.

## Feature engineering layer

Lives in `src/features/`. Optional, opt-in from the backtest CLI via
`--evaluate-features`. Public entry: `build_feature_evaluation(symbols,
*, start, end, forward_horizon=5)` returns a `FeatureEvaluation`
attached to `BacktestReport.features_evaluation`.

### Three feature families

- **`alpha158.py` — Alpha158-lite (pandas):** ~21 features tracking
  Qlib's Alpha158 naming convention so a future swap to the real
  handler is column-compatible. Includes K-line shape (`KMID, KLEN,
  KUP, KLOW, KMID2, KSFT`), rate-of-change (`ROC5/10/20/60`),
  moving-average distance (`MA5/20/60`), volatility (`STD5/20`),
  trend (`BETA20`), volume (`VMA5/20`), position-of-extreme
  (`IMAX5, IMIN5`), and rolling-high distance (`QTLU20`).
  Computed from a single symbol's OHLCV — no Qlib runtime required.
- **`alpha158.try_qlib_alpha158()` — real Qlib pass-through:**
  attempts `qlib.contrib.data.handler.Alpha158`. Requires (1)
  `pyqlib` importable and (2) a populated Qlib bin-data directory
  (`~/.qlib/qlib_data/{us_data,cn_data}` by default, or pass
  `provider_uri=`). Returns `None` on any failure — callers fall
  back to the pandas features above.
- **`custom.py` — scanner / narrative / sector features:**
  - *Scanner-derived*: `RSI14_Z60`, `DIST_SMA50_Z60`,
    `DIST_SMA200_Z60`, `DRAWDOWN_120`, `DRAWDOWN_120_Z60`,
    `PULLBACK_DEPTH`, `TREND_REGIME`. Built from the
    `add_indicators` columns so detector and feature inputs stay in
    sync.
  - *Sector-relative*: `SECTOR_RELATIVE_5D / _20D` — symbol return
    minus the sector cohort's median return. Captures "leading the
    group" vs "lagging the group."
  - *Narrative*: `NARRATIVE_SCORE` — the live 0..1 score broadcast
    onto the most recent bar per symbol. yfinance has no historical
    news backfill so this column is mostly NaN by design; the
    evaluator simply drops NaN rows, so missing narrative never
    breaks the run.

### Evaluation (IC / IR)

`evaluator.evaluate_features(panel, close_panel, forward_horizon=5)`:

- For each in-window date, computes the Spearman rank correlation
  between feature value and forward N-day return across the
  cross-section. Dates with fewer than `MIN_SYMBOLS_PER_DATE`
  observations are skipped.
- Aggregates per-feature: `mean_ic`, `std_ic`, `ir = mean/std`,
  `t_stat = mean / (std/√N)`, `n_periods`, `n_observations`.
- Features with fewer than `MIN_OBSERVATIONS` non-null pairs are
  dropped entirely so a sparse signal can't produce a misleading IR.
- Result is sorted by `|IR|` descending — the dashboard / report just
  shows the head.

Rule of thumb: `|IR| > 1.0` is a real signal worth investigating;
`|IR| < 0.3` is noise. These thresholds (`STRONG_IR`, `WEAK_IR`) are
in `src/backtest/refine.py`.

### Refinement heuristics on features

`refine._feature_ic_checks` adds two new rules to the suggestions log:

- **Promote**: a feature with `|IR| ≥ STRONG_IR` that *isn't already
  a scanner input* is flagged as a candidate for a new pattern or as
  an additional factor in an existing detector. Cap of 3 promotions
  per run so the journal stays focused.
- **Deprecate**: a feature that *is* already a scanner input
  (matched substring against `_SCANNER_INPUTS`) but reports
  `|IR| < WEAK_IR` is flagged as overweighted — consider lowering
  its component weight or moving it from a gate to a soft signal.

### Running with features

```bash
python -m src.backtest.run --evaluate-features --symbols NVDA,PLTR,AMD,AVGO --start 2024-01-01
python -m src.backtest.run --evaluate-features --sector AI Chips --start 2024-01-01
python -m src.backtest.run --evaluate-features --feature-horizon 10 --start 2024-01-01
```

The CLI prints the top five features by `|IR|` after the metrics
summary, and the per-run markdown report includes a full feature
table. Suggestions land in `logs/suggestions.md` alongside the
pattern-level ones.

### Adding a new feature

1. If it's a single-symbol indicator: add the column in
   `compute_alpha158_lite` (for Alpha158-style) or
   `compute_per_symbol` (for scanner-derived); register the name in
   the module's `*_FEATURES` tuple.
2. If it needs a cross-section: add a function in `custom.py` that
   takes the long-form panel + wide close panel and returns the
   joined panel — follow `add_sector_relative` as a template.
3. Tag its category in `pipeline._category_map`.
4. Add a test that exercises the feature on synthetic data with a
   known signal direction.

## Deep research layer (scaffold)

Lives in `src/research/`. Implements the mission's *supporting capability*
— a secondary validation that activates only on the highest-conviction
setups. This is currently a **scaffold**: protocol + dataclass + null
baseline, no real research implementation yet. The shape is fixed now
so the scanner can be wired to it later without churning call sites.

### Contract

- **`ResearchResult`** — frozen dataclass with the mission's checklist
  as free-form slots: `summary`, `company_quality`, `management`,
  `partnerships`, `financial_health`, `key_risks`, plus `sources`,
  `confidence ∈ [0, 1]`, and a `raw` escape hatch for richer payloads.
  `to_row()` flattens it for table rendering, mirroring the
  `NarrativeResult.to_row()` pattern.
- **`Researcher`** — `@runtime_checkable` protocol with one method:
  `research(ticker) -> ResearchResult`. Implementations may be slow
  and network-bound but must never raise — return an empty result
  with `confidence=0.0` instead. A failing researcher must never
  abort a scan.
- **`NullResearcher`** — baseline that returns an empty result. Lets
  the integration layer call `researcher.research(sym)` unconditionally
  during wiring without branching.

### Conviction gate

The scanner must never spend a research call on a weak match. Two
helpers centralize the policy:

- **`should_research(score, *, threshold=70.0)`** — single yes/no on a
  composite score. Default `DEFAULT_CONVICTION_THRESHOLD = 70.0`
  matches the dashboard's "70+ clean setup" rule of thumb.
- **`top_candidates(matches, *, threshold=70.0, limit=5)`** — filter
  by threshold then take the top-N by `effective_score`. Caps the
  per-run cost of research even on a wildly bullish day.

### Current implementation: `LLMResearcher`

The first real `Researcher` ships as `src/research/llm_researcher.py`.
It is an Anthropic-SDK-backed synthesizer that turns the same news
basket the narrative scorer collected into a fundamental read on the
mission's six checklist slots.

- **Model**: `claude-sonnet-4-6` by default (`DEFAULT_MODEL`). Sonnet
  hits the right speed/quality point for ~5 short structured-extraction
  calls per scan. Override per-instance — bump to `claude-opus-4-7`
  when downstream eval shows Sonnet missing nuance on management or
  risks; drop to `claude-haiku-4-5` only when even Sonnet feels slow.
- **Prompt caching**: the system prompt (rubric + worked examples +
  format rules) is marked `cache_control: {"type": "ephemeral"}` and
  is kept as a module-level constant so the bytes are stable across
  calls. The per-ticker payload (symbol, headlines, today's date)
  lives in the *user* turn so the cached prefix is never invalidated.
  Confirm caching is active by checking `response.usage.
  cache_read_input_tokens > 0` on the second call of a session.
- **Structured output**: `output_config.format` with a strict
  JSON schema mirroring `ResearchResult`. No prompt-engineered "return
  JSON" hacks, no regex parsing — the API guarantees a parseable
  response shape.
- **Headlines source**: defaults to `src.narrative.sources.
  default_sources()` (Polygon + yfinance) so the researcher and the
  scorer see the same basket. Pass `sources=[...]` to inject a custom
  feed or `sources=[]` to disable the fetch entirely (useful for
  benchmark prompts where the basket should not affect the read).
- **Failure mode**: any failure — news fetch, API error, malformed
  JSON, missing fields — is logged and returned as an empty
  `ResearchResult` with `confidence=0.0`. Honors the same convention
  the narrative + qlib layers follow: optional layers degrade to a
  no-op, never abort a scan.
- **Latency**: `max_tokens=1024`, per-call timeout 30s via
  `client.with_options(timeout=...)`. No streaming — the six short
  fields render well under the SDK's non-streaming timeout.
- **Cost shape**: one API call per *unique* high-conviction symbol
  per scan. At Sonnet 4.6 prices with prompt caching, a five-symbol
  scan costs cents, not dollars.

`LLMResearcher` is still **opt-in**. It is wired into the package
export but not yet into `Scanner` or `MatchResult` — see the planned
integration plan below. To use it standalone today:

```python
from src.research import LLMResearcher

r = LLMResearcher()
result = r.research("NVDA")     # never raises
print(result.summary, result.key_risks, result.confidence)
```

Requires `anthropic` (now in `requirements.txt`) and
`ANTHROPIC_API_KEY` in `.env`. Without the key, the constructor
succeeds and the first `.research()` call returns an empty result
with `confidence=0.0` — same contract as a network failure.

### Wired integration (current behavior)

The research layer is now wired through `Scanner` and the dashboard.
It stays strictly *opt-in*: with no researcher configured, the scanner
is byte-identical to before — `match.research` is always `None` and
zero LLM calls happen.

- `MatchResult` carries a nullable `research: ResearchResult | None`
  field alongside `narrative`. `to_row()` exposes `research_summary`
  and `research_confidence` columns (empty defaults when no research).
- `Scanner(researcher=..., research_limit=5)` wires the layer. The
  enrichment runs *after* the narrative pass and after the
  `min_score` filter + sort — so research operates on the final
  ranked list and never re-orders it.
- Selection uses `top_candidates(matches, threshold=
  DEFAULT_CONVICTION_THRESHOLD, limit=research_limit,
  unique_by="symbol")`. A symbol that hits two patterns at once
  (NVDA on Trend Rider + Bottom Hunter) consumes exactly **one**
  research slot; the resulting `ResearchResult` is attached to both
  of that symbol's matches.
- One `researcher.research(symbol)` call per unique high-conviction
  symbol. Same one-fetch-per-symbol economics as the narrative pass.
- A researcher failure on any one symbol is logged and skipped —
  that symbol's matches keep `research=None` and the rest of the
  scan completes normally. Honors the same convention narrative +
  qlib follow.
- The dashboard's per-match detail expander renders a collapsible
  "🔬 Deep research (fundamental read)" subsection when a research
  payload is present: confidence progress bar, summary, then
  `company_quality`, `management`, `partnerships`, `financial_health`,
  `key_risks`. A researcher fail-soft (empty payload, confidence 0)
  shows a one-line caption instead of an empty expander.

#### Wiring it on

```python
from src.scanner import Scanner
from src.narrative import NarrativeScorer
from src.research import LLMResearcher

scanner = Scanner(
    narrative_scorer=NarrativeScorer(),
    researcher=LLMResearcher(),     # opt-in; needs ANTHROPIC_API_KEY
    research_limit=5,
)
report = scanner.scan_discovery(sectors=["AI", "Chips"])
```

#### Cost shape

Per scan, at most `research_limit` Claude API calls — one per unique
high-conviction symbol. With Sonnet 4.6 + prompt caching, five
research calls cost cents, not dollars. The discovery sweep itself
(fetch + indicators + detectors + narrative) is unchanged and
remains the fast primary engine.

#### Tuning knobs

- `research_limit=N` — raise for broader coverage on bullish days,
  lower (or set to `0`-equivalent by passing `researcher=None`) to
  disable.
- The conviction floor `DEFAULT_CONVICTION_THRESHOLD = 70.0` is the
  single gate in `src/research/base.py`. To research a wider band
  (e.g. include 65+ when a strong catalyst is present), change it
  there rather than scattering thresholds across the scanner.
- To swap the research backend (e.g. analyst notes, a different
  LLM), implement the `Researcher` protocol and pass the instance
  to `Scanner(researcher=...)` — no other call sites change.

### Adding a real researcher

1. Implement the `Researcher` protocol in a new module under
   `src/research/`, e.g. `src/research/llm_research.py` defining
   `LLMResearcher(name="llm", ...).research(ticker) -> ResearchResult`.
2. Wire it through `Scanner(researcher=LLMResearcher(...))`.
3. Add an integration test that confirms the scanner only invokes
   `research()` on symbols clearing `DEFAULT_CONVICTION_THRESHOLD`
   and never more than `research_limit` times per run.

## Modal Compute Layer

Lives in `src/modal_app/` and `src/data/historical.py`. **Optional and
opt-in.** Importing `src.modal_app` never imports the `modal` client at
module load time — the dashboard, scanner, and daily workflows are
completely unaffected by its presence or absence.

### Purpose

Modal gives us a fast, on-demand compute fabric for the *heavy* parts
of the workflow that don't belong in a Codespace:

- Cold-pulling 5+ years of daily OHLCV for 1k–5k symbols in parallel.
- Running full-universe backtests + feature evaluation against a
  consistent, version-locked container.
- (Future) hosting the intelligence / narrative-research layer when it
  needs to fan out across many tickers without local resource limits.

Two design principles drive everything in this layer:

1. **The same code runs locally and on Modal.** Detectors, narrative
   scorer, backtest engine, feature pipeline — all of it is just the
   `src/` tree, shipped into the container via
   `add_local_python_source`. There is no Modal-only fork of any
   business logic.
2. **Environment, not branches, decides where data lives.** Functions
   in `src.data.historical` resolve their on-disk root from
   `STOCK_DATA_ROOT`. Locally that defaults to `data/historical/`; on
   Modal the function entrypoints set it to `/data/historical` (the
   mounted volume). Nothing else in the project needs to know.

### Historical OHLCV store

Distinct from the dashboard's `data/cache/` (short-lookback, daily
refresh). The historical store is the long-term, durable home for full
price history, owned by `src/data/historical.py`.

- **Layout**: `data/historical/{SYMBOL}.parquet`, one file per
  ticker, OHLCV columns, tz-naive `date` index, monotonic + unique.
- **First pull**: ~5 years of daily bars (`DEFAULT_FULL_HISTORY_DAYS`).
- **Incremental updates**: `update_symbol` reads the last stored bar,
  requests just the gap (plus a 7-day pad for non-trading days),
  merges, and atomic-writes via a `.tmp` rename so a crashed write
  cannot corrupt the store.
- **Source policy**: Polygon-first, yfinance-fallback — same order as
  `src/data/fetcher.py` but routed through the historical store's own
  fetcher so it cannot accidentally pollute `data/cache/`.
- **Failure mode**: a single-symbol failure logs and returns an
  `UpdateResult(status="error")`. A 5k-name sweep keeps going.
- **Parallelism**: `download_universe(symbols, max_workers=N)` uses a
  thread pool — the underlying clients are HTTP-bound. Default 8
  workers; Modal jobs override to 16.

#### Loading data for backtests / features

```python
from src.data import load_history, load_panel

df = load_history("NVDA", start="2024-01-01")   # one symbol, sliced
frames = load_panel(["NVDA", "PLTR", "AMD"])    # {symbol: DataFrame}
closes = load_panel(syms, field_name="close")   # wide DataFrame for x-section work
```

The existing backtest path in `src/backtest/` still calls
`fetch_ohlcv`. To run a backtest *on top of* the historical store
without modifying that code, use `warm_cache_from_historical(symbols)`
first — it copies the relevant parquets into `data/cache/` and
touches their mtime so the fetcher treats them as fresh hits. Modal's
`run_backtest_remote` does this automatically.

#### CLI

```bash
# Local
python -m src.data.historical download --symbols NVDA,PLTR
python -m src.data.historical download --sector "AI" "Chips"
python -m src.data.historical download --all --workers 12
python -m src.data.historical info NVDA
python -m src.data.historical list
```

`make historical-download` / `make historical-list` cover the common
cases.

### Modal app

`src/modal_app/app.py` defines:

- **App**: `modal.App("stock-finder")`.
- **Image**: `debian_slim(python_version="3.12")` + project
  `requirements.txt` + the live `src/` tree.
- **Volume**: `modal.Volume.from_name("stock_data",
  create_if_missing=True)`, mounted at `/data`.
- **Secrets**: `modal.Secret.from_dotenv(path=".env")` ships the
  developer's local keys (Polygon, Anthropic, …) into the container at
  job-submission time. No separate `modal secret create` step
  required for solo dev; teams can swap to `Secret.from_name(...)` in
  one line.

Two remote functions:

- **`download_universe_remote(symbols, force=False, max_workers=16)`** —
  refreshes the historical store, commits the volume, returns a
  summary dict.
- **`run_backtest_remote(symbols, start, end, ...)`** — optionally
  refreshes the symbols it needs, warms the cache from the historical
  store, calls the existing `run_backtest` + `write_report`, writes
  the markdown report under `/data/backtests/<timestamp>/`, returns
  the compact summary the local CLI prints.

Both have `@app.local_entrypoint` wrappers so you can fire them with a
single `modal run` command:

```bash
modal run src.modal_app.app::download --symbols NVDA,PLTR,AMD
modal run src.modal_app.app::backtest --symbols NVDA,PLTR --start 2024-01-01 --end 2026-05-01
```

`make modal-download SYMBOLS=NVDA,PLTR` and
`make modal-backtest SYMBOLS=NVDA,PLTR START=2024-01-01 END=2026-05-01`
are the Makefile shorthands.

### Triggering heavy jobs from Codespaces

The `src/modal_app/local_runner.py` helper exists for code (not CLI
users) that wants to fire remote jobs from inside the repo. It is the
import boundary the future intelligence layer will sit behind:

```python
from src.modal_app import local_runner

summary = local_runner.run_backtest(
    symbols=["NVDA", "PLTR", "AMD"],
    start="2024-01-01", end="2026-05-01",
    evaluate_features=True,
)
print(summary["report_path"], summary["win_rate"])
```

`local_runner` imports `modal` lazily so its mere import is free even
when no remote call is about to happen.

### Tool registry for the future intelligence layer

`src/modal_app/AVAILABLE_TOOLS` is a static, JSON-schema-friendly
catalog of the remote functions. The intent: a LangGraph / Claude
agent that decides to "go run a backtest" can wrap each entry as a
tool with one comprehension:

```python
from src.modal_app import available_tools
from src.modal_app import local_runner

tools = {
    t["name"]: getattr(local_runner, t["name"])
    for t in available_tools()
}
# tools["run_backtest"](symbols=..., start=..., end=...)
```

Adding a new remote function is therefore a three-step extension:
write the `@app.function`, write the matching `local_runner` wrapper,
add an entry to `AVAILABLE_TOOLS`. The agent picks it up
automatically.

### Cost shape

- A 5000-symbol cold download on a 4-CPU container with 16 workers
  finishes in minutes, dominated by HTTP latency from the data
  providers. Subsequent incremental updates pull only the day-or-two
  gap per symbol.
- Per backtest: one container start, plus the same wall-clock cost as
  running it locally. The volume read is free; results land back on
  the volume so a re-run is even cheaper.

## Conventions

- Type hints everywhere; dataclasses for structured returns.
- No hard-coded API keys — read from `.env` via `src/config/settings.py`.
- Avoid network I/O at import time.
- Tests under `tests/` (synthetic data, no network).
- Indicator column names are constants in `indicators.py`, not literals.
- Detectors are pure functions of an indicator-augmented DataFrame.
- Optional / opt-in layers (narrative, research, qlib features) must
  degrade to a no-op rather than abort the run. Missing data is the
  default — every layer plans for it.
