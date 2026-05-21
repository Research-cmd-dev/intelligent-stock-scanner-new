"""Streamlit dashboard for the Stock Finder Agent.

Thin view layer — all heavy lifting (fetch, indicators, pattern detection,
narrative scoring) happens in :mod:`src.scanner` and :mod:`src.narrative`.
The dashboard's job is to collect inputs, call :func:`run_scan`, and
render the resulting :class:`ScanReport`.

Launch with::

    streamlit run src/dashboard/app.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import streamlit as st

from src.config.sectors import SECTORS
from src.scanner import MatchResult, ScanReport, Scanner
from src.scanner.patterns import ALL_DETECTORS, BOTTOM_HUNTER, TREND_RIDER


# ---------------------------------------------------------------------- #
# Page config                                                            #
# ---------------------------------------------------------------------- #

st.set_page_config(
    page_title="Stock Finder Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


PATTERN_LABELS: dict[str, str] = {
    TREND_RIDER: "Trend Rider",
    BOTTOM_HUNTER: "Bottom Hunter",
}


# ---------------------------------------------------------------------- #
# Scan inputs (hashable so @st.cache_data can key off them)              #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScanParams:
    """Everything that determines a scan result — used as a cache key."""

    mode: str                       # "discovery" | "watchlist"
    sectors: tuple[str, ...]        # () means "all sectors" in discovery mode
    watchlist: tuple[str, ...]
    patterns: tuple[str, ...]
    min_score: float
    lookback_days: int
    include_sector_etfs: bool
    include_theme_etfs: bool
    include_broad_market: bool
    with_narrative: bool
    narrative_weight: float


# ---------------------------------------------------------------------- #
# The scan itself, cached                                                #
# ---------------------------------------------------------------------- #


@st.cache_data(show_spinner=False, ttl=60 * 60)
def cached_scan(params: ScanParams) -> ScanReport:
    """Run a scan and cache by exact parameter tuple.

    TTL of 1 hour keeps the dashboard snappy while still picking up new
    bars within the trading day. Disk caches in the fetcher and news
    layer dedupe across runs that miss this in-memory layer.
    """
    detectors = tuple(
        (name, fn) for name, fn in ALL_DETECTORS if name in params.patterns
    )
    if not detectors:
        return ScanReport(matches=[])

    scorer = None
    if params.with_narrative:
        from src.narrative import NarrativeScorer
        scorer = NarrativeScorer()

    scanner = Scanner(
        detectors=detectors,
        min_score=params.min_score,
        lookback_days=params.lookback_days,
        narrative_scorer=scorer,
        narrative_weight=params.narrative_weight,
    )

    if params.mode == "watchlist":
        return scanner.scan_watchlist(list(params.watchlist))

    return scanner.scan_discovery(
        sectors=list(params.sectors) if params.sectors else None,
        include_sector_etfs=params.include_sector_etfs,
        include_theme_etfs=params.include_theme_etfs,
        include_broad_market=params.include_broad_market,
    )


# ---------------------------------------------------------------------- #
# Sidebar                                                                #
# ---------------------------------------------------------------------- #


def render_sidebar() -> ScanParams | None:
    """Collect scan inputs. Returns ``None`` until the user clicks Run."""
    st.sidebar.title("📈 Stock Finder")
    st.sidebar.caption("Daily scan over high-conviction thematic sectors.")

    mode = st.sidebar.radio(
        "Scan mode",
        options=["Discovery", "Watchlist"],
        help=(
            "**Discovery** sweeps the curated sector universe. "
            "**Watchlist** scans only the symbols you supply."
        ),
        horizontal=True,
    )

    sectors: list[str] = []
    watchlist: list[str] = []
    include_sector_etfs = True
    include_theme_etfs = True
    include_broad_market = False

    if mode == "Discovery":
        all_sectors = sorted(SECTORS.keys())
        sectors = st.sidebar.multiselect(
            "Sectors",
            options=all_sectors,
            default=all_sectors,
            help="Empty = no single stocks (ETFs only, if enabled).",
        )
        with st.sidebar.expander("ETF coverage", expanded=False):
            include_sector_etfs = st.checkbox(
                "SPDR sector ETFs (XLK, XLE, …)", value=True
            )
            include_theme_etfs = st.checkbox(
                "Theme ETFs (SMH, URA, XBI, …)", value=True
            )
            include_broad_market = st.checkbox(
                "Broad market (SPY / QQQ / IWM)", value=False
            )
    else:
        text = st.sidebar.text_area(
            "Symbols (one per line or comma-separated)",
            value="NVDA\nPLTR\nRKLB\nLLY",
            height=140,
        )
        watchlist = _parse_watchlist(text)
        st.sidebar.caption(f"{len(watchlist)} symbol(s) parsed")

    st.sidebar.markdown("---")
    st.sidebar.subheader("Patterns")
    use_trend = st.sidebar.checkbox("Trend Rider", value=True)
    use_bottom = st.sidebar.checkbox("Bottom Hunter", value=True)
    patterns: list[str] = []
    if use_trend:
        patterns.append(TREND_RIDER)
    if use_bottom:
        patterns.append(BOTTOM_HUNTER)

    st.sidebar.markdown("---")
    st.sidebar.subheader("Tuning")
    min_score = st.sidebar.slider(
        "Minimum score", min_value=0, max_value=100, value=50, step=5,
        help="Drop matches whose composite score is below this.",
    )
    lookback_days = st.sidebar.slider(
        "Lookback (days)", min_value=180, max_value=540, value=300, step=30,
        help="History per symbol. Needs ~300+ for SMA200.",
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("Narrative overlay")
    with_narrative = st.sidebar.checkbox(
        "Score recent news (Polygon + yfinance)",
        value=True,
        help="Blends pattern score with news sentiment for matched symbols only.",
    )
    narrative_weight = st.sidebar.slider(
        "Narrative weight", min_value=0.0, max_value=0.5, value=0.20, step=0.05,
        disabled=not with_narrative,
        help="0 = pattern-only; 0.20 default = 80% pattern / 20% narrative.",
    )

    st.sidebar.markdown("---")
    run = st.sidebar.button(
        "🚀 Run Full Scan Now", type="primary", use_container_width=True
    )
    st.sidebar.caption("Cached for 1 hour per parameter set.")

    if not run:
        return None

    if not patterns:
        st.sidebar.error("Enable at least one pattern.")
        return None
    if mode == "Watchlist" and not watchlist:
        st.sidebar.error("Add at least one symbol to the watchlist.")
        return None

    return ScanParams(
        mode="discovery" if mode == "Discovery" else "watchlist",
        sectors=tuple(sectors),
        watchlist=tuple(watchlist),
        patterns=tuple(patterns),
        min_score=float(min_score),
        lookback_days=int(lookback_days),
        include_sector_etfs=include_sector_etfs,
        include_theme_etfs=include_theme_etfs,
        include_broad_market=include_broad_market,
        with_narrative=with_narrative,
        narrative_weight=float(narrative_weight),
    )


def _parse_watchlist(text: str) -> list[str]:
    """Accept newline- or comma-separated tickers; normalize to uppercase."""
    raw = text.replace(",", "\n").split("\n")
    return sorted({s.strip().upper() for s in raw if s.strip()})


# ---------------------------------------------------------------------- #
# Main panel rendering                                                   #
# ---------------------------------------------------------------------- #


def render_header() -> None:
    st.title("Stock Finder Agent")
    st.caption(
        "Trend Rider + Bottom Hunter scans across high-conviction thematic "
        "sectors, narrated with news sentiment."
    )


def render_empty_state() -> None:
    st.info(
        "Configure your scan in the sidebar, then click "
        "**Run Full Scan Now** to see matches.",
        icon="👈",
    )
    with st.expander("How scoring works", expanded=False):
        st.markdown(
            """
            - **Pattern score (0–100)** is the detector's own conviction.
              70+ = clean setup, 50–70 = worth a look, <50 rarely surfaces.
            - **Narrative score (0–1)** aggregates recent news. 0.5 is
              neutral / no signal.
            - **Composite** blends the two (default 80% pattern, 20% news).
              When the narrative overlay is off, composite = pattern.
            - Rankings always use the composite when available.
            """
        )


def render_summary(report: ScanReport, params: ScanParams) -> None:
    matches = report.matches
    cols = st.columns(5)

    cols[0].metric("Matches", len(matches))

    if matches:
        avg = sum(m.effective_score for m in matches) / len(matches)
        top = max(m.effective_score for m in matches)
        unique_sectors = {s for m in matches for s in m.sectors}
        unique_tickers = {m.symbol for m in matches}
    else:
        avg = top = 0.0
        unique_sectors = set()
        unique_tickers = set()

    cols[1].metric("Avg score", f"{avg:.1f}")
    cols[2].metric("Top score", f"{top:.1f}")
    cols[3].metric("Unique tickers", len(unique_tickers))
    cols[4].metric("Sectors hit", len(unique_sectors))

    coverage = report.coverage or {}
    if coverage:
        st.caption(
            f"Universe: **{coverage.get('universe', 0)}** symbols "
            f"→ fetched **{coverage.get('fetched', 0)}** "
            f"→ **{coverage.get('matches', 0)}** matches "
            f"(min score {params.min_score:g}, "
            f"narrative {'on' if params.with_narrative else 'off'})."
        )

    if matches:
        # Surface data source mix so users on Polygon free tier can see how
        # many hits came from the primary provider vs yfinance fallback.
        from collections import Counter

        src_counts = Counter(getattr(m, "source", None) for m in matches)
        src_counts = {k: v for k, v in src_counts.items() if k}  # drop None
        if src_counts:
            pretty = ", ".join(f"{k} {v}" for k, v in sorted(src_counts.items()))
            st.caption(f"Sources: {pretty}.")


def render_table(report: ScanReport, params: ScanParams) -> None:
    if not report.matches:
        st.warning(
            "No matches passed the minimum-score threshold. "
            "Try lowering the floor or widening sector coverage."
        )
        return

    display = _build_display_df(report.matches, with_narrative=params.with_narrative)

    column_config: dict[str, object] = {
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Pattern": st.column_config.TextColumn("Pattern", width="small"),
        "Score": st.column_config.ProgressColumn(
            "Score", format="%.1f", min_value=0, max_value=100, width="small",
        ),
        "Price": st.column_config.NumberColumn("Price", format="$%.2f", width="small"),
        "Sector": st.column_config.TextColumn("Sector", width="medium"),
        "Key Signal": st.column_config.TextColumn("Key Signal", width="large"),
        "As Of": st.column_config.DateColumn("As Of", width="small"),
    }
    if params.with_narrative:
        column_config["Composite"] = st.column_config.ProgressColumn(
            "Composite", format="%.1f", min_value=0, max_value=100, width="small",
        )
        column_config["Narrative"] = st.column_config.ProgressColumn(
            "Narrative", format="%.2f", min_value=0.0, max_value=1.0, width="small",
        )
        column_config["Narrative Explanation"] = st.column_config.TextColumn(
            "Narrative Explanation", width="large",
        )

    st.dataframe(
        display,
        column_config=column_config,
        hide_index=True,
        use_container_width=True,
    )

    csv = display.to_csv(index=False).encode("utf-8")
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    st.download_button(
        "⬇️ Download CSV",
        data=csv,
        file_name=f"stock_finder_matches_{timestamp}.csv",
        mime="text/csv",
    )


def _build_display_df(
    matches: list[MatchResult], *, with_narrative: bool
) -> pd.DataFrame:
    """Project MatchResults into the columns the user asked for."""
    rows: list[dict[str, object]] = []
    for m in matches:
        row: dict[str, object] = {
            "Ticker": m.symbol,
            "Pattern": PATTERN_LABELS.get(m.pattern, m.pattern),
            "Score": round(m.score, 1),
            "Price": round(m.price, 2),
            "Sector": ", ".join(m.sectors) if m.sectors else "—",
            "Key Signal": _key_signal(m),
            "As Of": pd.to_datetime(m.as_of).date(),
        }
        if with_narrative:
            row["Composite"] = round(m.effective_score, 1)
            row["Narrative"] = (
                round(m.narrative.score, 3) if m.narrative else None
            )
            row["Narrative Explanation"] = (
                m.narrative.explanation if m.narrative else ""
            )
        rows.append(row)

    df = pd.DataFrame(rows)
    if with_narrative:
        # Reorder so the ranking column is adjacent to the inputs that feed it.
        cols = [
            "Ticker", "Pattern", "Score", "Narrative", "Composite",
            "Price", "Sector", "Key Signal", "Narrative Explanation", "As Of",
        ]
        df = df[cols]
    return df


def _key_signal(m: MatchResult) -> str:
    """One-line summary of why this match scored — pick the top factor."""
    if not m.factors:
        return ""
    top = max(m.factors, key=lambda f: f.contribution)
    if top.note:
        return f"{top.name}: {top.note} ({top.contribution:.0f}/{top.weight:.0f} pts)"
    return f"{top.name} {top.value:.2f} ({top.contribution:.0f}/{top.weight:.0f} pts)"


def render_detail(report: ScanReport, params: ScanParams) -> None:
    """Per-match deep-dive: factor breakdown + indicators + top headlines."""
    if not report.matches:
        return
    with st.expander("Per-match detail", expanded=False):
        choices = [
            f"{m.symbol} — {PATTERN_LABELS.get(m.pattern, m.pattern)} "
            f"({m.effective_score:.1f})"
            for m in report.matches
        ]
        idx = st.selectbox(
            "Inspect a match",
            options=list(range(len(report.matches))),
            format_func=lambda i: choices[i],
        )
        m = report.matches[idx]

        left, right = st.columns(2)
        with left:
            st.markdown(f"**{m.symbol} — {PATTERN_LABELS.get(m.pattern, m.pattern)}**")
            st.write(f"Pattern score: **{m.score:.1f}** / 100")
            if m.composite_score is not None and m.composite_score != m.score:
                st.write(f"Composite: **{m.composite_score:.1f}** / 100")
            st.write(f"Price (as of {pd.to_datetime(m.as_of).date()}): "
                     f"${m.price:.2f}")
            st.write(f"Source: {m.source}")
            if m.sectors:
                st.write(f"Sectors: {', '.join(m.sectors)}")
            if m.themes:
                st.write(f"Themes: {', '.join(m.themes)}")
        with right:
            if m.factors:
                fac_df = pd.DataFrame([
                    {
                        "Factor": f.name,
                        "Value": round(f.value, 3),
                        "Contribution": round(f.contribution, 1),
                        "Weight": round(f.weight, 1),
                        "Note": f.note,
                    }
                    for f in m.factors
                ])
                st.dataframe(fac_df, hide_index=True, use_container_width=True)

        if m.narrative:
            st.markdown("**Narrative read**")
            st.write(m.narrative.explanation)
            if m.narrative.top_items:
                for item in m.narrative.top_items:
                    age = (datetime.utcnow() - item.published_utc.replace(tzinfo=None)).days \
                        if item.published_utc.tzinfo \
                        else (datetime.utcnow() - item.published_utc).days
                    when = "today" if age <= 0 else f"{age}d ago"
                    publisher = item.publisher or item.provider
                    if item.url:
                        st.markdown(f"- [{item.title}]({item.url}) — _{publisher}, {when}_")
                    else:
                        st.markdown(f"- {item.title} — _{publisher}, {when}_")

        _render_research_section(m)


def _render_research_section(m: MatchResult) -> None:
    """Deep-research read for high-conviction matches.

    Renders only when a research payload exists. Collapsed by default so
    the dashboard's idea-generation view stays tight — research is the
    *secondary* validation layer, not the primary surface.
    """
    research = m.research
    if research is None:
        return
    # Researcher fail-soft path returns confidence=0 + empty fields.
    # Don't show an empty expander — surface a small caption instead.
    has_content = bool(
        research.summary or research.company_quality or research.management
        or research.partnerships or research.financial_health or research.key_risks
    )
    if not has_content:
        st.caption("🔬 Deep research attempted but returned no content (low confidence).")
        return

    with st.expander("🔬 Deep research (fundamental read)", expanded=False):
        st.progress(
            min(max(research.confidence, 0.0), 1.0),
            text=f"Researcher confidence: {research.confidence:.2f}",
        )
        if research.summary:
            st.markdown(f"**Summary** — {research.summary}")

        sections = [
            ("Company quality", research.company_quality),
            ("Management", research.management),
            ("Partnerships", research.partnerships),
            ("Financial health", research.financial_health),
            ("Key risks", research.key_risks),
        ]
        for label, body in sections:
            if not body:
                continue
            st.markdown(f"**{label}**")
            st.write(body)

        if research.sources:
            st.caption(
                f"Grounded in {len(research.sources)} headline"
                f"{'s' if len(research.sources) != 1 else ''} from the narrative basket."
            )


def render_diagnostics(report: ScanReport) -> None:
    if not report.errors:
        return
    with st.expander(f"Diagnostics ({len(report.errors)} symbol issue(s))", expanded=False):
        diag_df = pd.DataFrame(
            [{"Symbol": s, "Issue": msg} for s, msg in sorted(report.errors.items())]
        )
        st.dataframe(diag_df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------- #
# Entry point                                                            #
# ---------------------------------------------------------------------- #


def main() -> None:
    render_header()
    params = render_sidebar()

    if params is None:
        render_empty_state()
        return

    with st.spinner("Scanning… fetching OHLCV, running detectors, scoring news…"):
        report = cached_scan(params)

    render_summary(report, params)
    render_table(report, params)
    render_detail(report, params)
    render_diagnostics(report)


if __name__ == "__main__":
    main()
