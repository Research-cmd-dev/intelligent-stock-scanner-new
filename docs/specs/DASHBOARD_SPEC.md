# Dashboard Spec: Signal Visualization and Narrative Validation

## Mission

Build the dashboard layer that turns the screener's forward-return outcomes
and narrative data into a visual review tool. Two purposes:

1. **Daily workflow**: scroll through flagged signals, look at the chart,
   read the narrative + research context, decide whether to act on it.
2. **Periodic validation**: empirically verify that the narrative layer
   is actually adding signal, and tune it based on what's working.

Built on top of the existing Streamlit app (`src/dashboard/app.py`).
Depends on the screener refactor (SCREENER_REFACTOR.md) being implemented
first — specifically `ForwardOutcome` and `ScreenerMetrics` must exist.

## Stack

- **streamlit-lightweight-charts** — TradingView's open-source charting
  library, wrapped for Streamlit. Used for the price/signal chart panel.
- **plotly** — All non-price visualizations (histograms, heatmaps, scatters,
  bar charts).
- **streamlit-plotly-events** — Click handling on Plotly charts to enable
  drill-down from metric → specific signal.
- No other charting libraries. No matplotlib, no Bokeh, no Altair.

Install:
```
pip install streamlit-lightweight-charts streamlit-plotly-events
```

## Tab structure

Four tabs in `src/dashboard/app.py`:

1. **Signal Browser** — daily workflow (chart + per-signal detail)
2. **Screener Metrics** — forward-return distributions and hit rates
3. **Universe Narrative** — cross-sectional theme and catalyst views
4. **Validation** — empirical proof that the narrative layer is signal

Order matters. Signal Browser is the daily-use tab; Validation is
periodic-use. Sort by usage frequency.

## State management

Use `st.session_state` to carry selected-signal state across tabs. When
the user clicks a row in a signals table, or a point in a scatter plot,
the Signal Browser auto-updates to show that signal. Specifically:

```python
st.session_state.selected_signal_id = None    # set to Signal.id when clicked
st.session_state.selected_symbol = None       # convenience for chart lookup
st.session_state.selected_date = None         # convenience for chart slice
```

Every cross-tab interactive plot writes to these on click; the Signal
Browser reads from them on render. Default state: most recent high-score
signal.

## Tab 1: Signal Browser

The daily-use view. Three panels stacked vertically.

### Panel A: Signal selection table

A sortable, filterable table of all signals from the most recent scan
(or filtered date range). Columns: date, symbol, pattern, composite
score, narrative score, sectors, forward return at chosen horizon
(if available from a prior screener run).

Use `st.dataframe` with `on_select="rerun"` and
`selection_mode="single-row"`. Clicking a row updates session state and
re-renders Panel B and C.

### Panel B: Chart view (Lightweight Charts)

When a signal is selected, render a chart for that symbol over a window
of `[signal_date - 90 days, signal_date + 252 days]`.

Components:
- Candlesticks (primary series)
- SMA20, SMA50, SMA200 as line overlays
- Volume as histogram in a lower pane (height ~20% of total)
- Signal marker: arrow above the bar for Trend Rider, arrow below for
  Bottom Hunter; label shows the composite score
- Shaded vertical band from signal_date forward 63 trading days (the
  default forward-return window)
- Optional toggle: overlay RSI14 in another lower pane

Implementation:

```python
from streamlit_lightweight_charts import renderLightweightCharts

# Build the chart config dict
chart_config = [
    {
        "chart": {"height": 500, ...},
        "series": [
            {"type": "Candlestick", "data": candle_data, ...},
            {"type": "Line", "data": sma20_data, "options": {"color": "blue"}},
            {"type": "Line", "data": sma50_data, "options": {"color": "orange"}},
            {"type": "Line", "data": sma200_data, "options": {"color": "red"}},
        ],
        "markers": [
            {"time": signal_date_str, "position": "aboveBar",
             "color": "green", "shape": "arrowDown",
             "text": f"TR {score:.0f}"},
        ],
    },
    {
        "chart": {"height": 100, ...},
        "series": [{"type": "Histogram", "data": volume_data}],
    },
]
renderLightweightCharts(chart_config, "signal_chart")
```

Use `@st.cache_data` on the OHLCV fetch + indicator computation keyed
on (symbol, date_range). Don't recompute when the user clicks a
different signal for the same symbol.

### Panel C: Signal detail card

Below the chart, render the full signal context as a clean markdown
block (or columned layout). Sections:

- **Header**: Symbol — Pattern — Composite Score (with progress bar)
- **Pattern factors**: a small Plotly horizontal bar showing the
  contribution of each Factor (`trend`, `pullback`, `rsi`, `slope`
  for Trend Rider) — uses the existing `MatchResult.factors`.
- **Narrative**: narrative score (with progress bar), top themes as
  pill-style chips, top catalysts as pill-style chips, narrative
  explanation text.
- **Research** (if conviction ≥70 and research ran): confidence bar,
  summary, expandable sections for company_quality, management,
  partnerships, financial_health, key_risks.
- **Forward returns**: small table showing returns at +1mo, +3mo,
  +6mo, +12mo (from `ForwardOutcome` if available, with MFE and
  days-to-peak).

This is the panel that replaces all the disparate dataclass outputs
with one coherent view of what the system thinks about this signal.

## Tab 2: Screener Metrics

All charts here use Plotly. Each chart filterable by sector, pattern,
score band, date range via sidebar controls.

### Chart 2.1: Forward-return distribution histogram

For a selected horizon (dropdown: 21d / 63d / 126d / 252d), histogram
of forward returns across all signals. Vertical lines at mean and
median. Annotation showing hit rates at common thresholds
(e.g., "31% gained 20%+, 12% gained 50%+, 4% gained 100%+").

```python
fig = px.histogram(outcomes_df, x=f"ret_{horizon}d", nbins=50)
fig.add_vline(x=mean_return, line_dash="dash", annotation_text="mean")
fig.add_vline(x=median_return, line_dash="dot", annotation_text="median")
```

### Chart 2.2: Hit-rate heatmap

Rows = horizons (21, 63, 126, 252), columns = thresholds (10%, 20%,
50%, 100%), color intensity = hit rate. Use `px.imshow` with text
annotations showing the actual percentages.

### Chart 2.3: Score band gradient

Bar chart with score bands (50-60, 60-70, 70-80, 80-90, 90-100) on
the x-axis and mean forward return on the y-axis (for selected
horizon). Error bars showing the IQR. **Critical chart**: tells you
whether higher composite scores actually correlate with better
outcomes. If the bars are flat, your scoring isn't calibrated.

### Chart 2.4: Sector breakdown

Horizontal bar chart of hit rate (at user-selected threshold) by
sector. Sort by hit rate. Tells you which sectors the scanner works
on and which it doesn't.

### Chart 2.5: MFE/MAE distribution

Side-by-side histograms of maximum favorable excursion and maximum
adverse excursion. Right tail of MFE histogram is where the
multi-baggers live. If MFE p95 is dramatically larger than the
median forward return, the system is right-tail driven (good for
asymmetric purposes — note this in the chart annotation).

## Tab 3: Universe Narrative

Cross-sectional views of narrative state across the watch universe.
Less critical than the other tabs, but useful for spotting
emerging themes.

### Chart 3.1: Theme × ticker heatmap

Rows = tickers (filterable to one sector at a time to keep the chart
readable), columns = themes from `themes.py`, cell color = aggregate
theme relevance from each ticker's most recent narrative scan.
`px.imshow` again. Sortable by row sum (most thematically loaded
tickers at top) or by a selected theme column.

Use case: pick "AI" sector, sort by "Power + Compute" column — see
all the AI names that also light up Power+Compute. That's the
multi-narrative intersection play you described.

### Chart 3.2: Catalyst frequency over time

Stacked area chart, x-axis = calendar weeks, y-axis = catalyst count,
stacked bands by catalyst kind (contract, funding, pivot, m&a, etc.).
Computed from cached narrative scans over the date range.

Use case: a sudden surge of "contract" catalysts in a sector signals
that real deals are happening — narrative is hardening into reality.

### Chart 3.3: Sentiment distribution by sector

Box plot, x-axis = sector, y-axis = narrative score, one box per sector
showing the distribution of narrative scores across that sector's
tickers (from latest scan). Identifies which sectors have the most
bullish coverage right now.

## Tab 4: Validation

The empirical "is this layer working" tab. Most important plots in the
whole dashboard.

### Chart 4.1: Narrative score vs forward return scatter

X-axis = narrative score at signal time (0-1), Y-axis = forward return
at selected horizon. Each dot = one historical signal. Color by pattern.
Include a regression line and report R² in the chart title.

**This is the single most important chart for evaluating the narrative
layer.** Positive slope = narrative is signal. Random scatter = narrative
is noise. If R² is near zero, that's a clear directive to either fix
or remove the narrative layer.

Wire click handler: click a dot → updates session state → user can
switch to Signal Browser to see that specific signal's chart.

### Chart 4.2: Theme × forward return bar chart

Bar chart of mean forward return (at selected horizon) grouped by
detected theme. Only include themes with ≥10 signals (gate on sample
size). Sort descending. Tells you which themes actually predicted
moves vs which are decorative.

### Chart 4.3: Catalyst × forward return bar chart

Same as 4.2 but for catalysts. Critical for tuning catalyst boost
weights — if "contract" predicts +15% mean return and "partnership"
predicts +2%, the boost weights should reflect that.

### Chart 4.4: Pattern-only vs Pattern+Narrative comparison

Two overlapping histograms of forward returns:
- Blue: signals where narrative_weight was effectively 0 (or filter
  to signals with no narrative data)
- Orange: signals with narrative weighting applied

Visual proof of whether narrative actually shifts the distribution
favorably. If the distributions overlap exactly, narrative is doing
nothing. If orange is right-shifted, narrative is helping.

Could also show as side-by-side box plots if the histograms overlap
too messily.

### Chart 4.5: Composite score vs forward return (the calibration test)

Same as Chart 2.3 (score band gradient) but specifically called out
here because it's the foundational validation: is your composite
scoring actually predictive? If signals with composite 90+ don't
outperform signals with composite 60-70, the whole scoring system
needs rethinking.

## Implementation order

Build these in priority order, one tab per Claude Code session:

1. **Tab 1 (Signal Browser)** — daily workflow. Without this, the
   dashboard isn't usable for the stated goal.
2. **Tab 4 (Validation)** — the empirical test. Tells us whether the
   narrative layer is worth keeping.
3. **Tab 2 (Screener Metrics)** — distribution stats. Most of this
   is rendering data the screener already produces.
4. **Tab 3 (Universe Narrative)** — exploratory views. Lowest
   priority because it's nice-to-have, not need-to-have.

Each tab is one focused implementation session. Don't let Claude Code
attempt more than one tab in a session.

## Data dependencies

This spec assumes the screener refactor has already produced:
- `ForwardOutcome` records for all historical signals
- `ScreenerMetrics` aggregated stats
- Per-signal `NarrativeResult` and (optionally) `ResearchResult` attached

If those aren't available yet for a given signal set, the relevant
charts should render with a "no data — run screener mode first"
message rather than crashing.

Cache strategy: all aggregations should be wrapped in `@st.cache_data`
keyed on (universe, date_range, min_score). The first render of each
tab can take a few seconds; subsequent interactions should be instant
because they're just filtering cached frames.

## Tests

New tests under `tests/`:

### `tests/test_dashboard_components.py`
- Synthetic outcomes → assert each Plotly figure builder returns a
  valid `plotly.graph_objects.Figure` with the expected trace count.
- Empty outcomes list → assert charts render an empty-state message
  rather than crashing.
- Single-signal selection → assert session state propagates correctly
  across tab boundaries.

Don't test the Lightweight Charts render directly (it's a JS component
in an iframe); test that the chart config dict is correctly built.

## Non-goals

- No real-time updating. Dashboard renders from cached/persisted data.
- No multi-user state. Single-user dashboard.
- No theming or branding work. Default Streamlit look is fine.
- Do not replace the existing dashboard panels (discovery scan,
  watchlist mode). Add the new tabs alongside.
- Do not implement alerting or notifications. That's a separate concern.

## Success criteria

After this spec is implemented:

1. User can run `streamlit run src/dashboard/app.py`, open the Signal
   Browser tab, see today's signals in a table, click one, and
   immediately see the chart with the signal marked plus all the
   narrative/research context next to it.
2. User can switch to the Validation tab and look at the
   narrative-score-vs-forward-return scatter, and from a single plot
   answer "is the narrative layer adding signal?" with confidence.
3. Clicking interactive points in Validation tab charts navigates the
   user to the Signal Browser pre-populated with the chosen signal.
4. All charts respond to sidebar filters (sector, pattern, score band,
   date range) consistently across tabs.
5. The dashboard remains responsive (sub-second interactions after
   initial cache warm-up) even on the full universe over 5+ years.
