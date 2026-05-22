# PROJECT.md

### Mission & Vision

**Primary Mission**  
To proactively identify emerging and under-appreciated narratives across high-growth sectors — including AI infrastructure, biotechnology, robotics, space, and beyond — and surface technically clean chart setups that offer strong asymmetric upside potential for longer-term investment.

**Core Philosophy**
- Technicals are the foundation — the chart must show something meaningful.
- Narrative and catalysts are major multipliers — they turn good technical setups into exceptional macro opportunities.
- Quality dramatically outweighs quantity — we would rather surface 5–10 high-conviction setups than 50 mediocre ones.
- Our job is to get within the ballpark on timing and clearly communicate conviction level so the user can make the final decision.

**Design Principles**
- Prioritize strong, clean technical patterns over many weak ones.
- Heavily reward alignment between technicals, narrative/themes, and catalysts.
- Create a transparent scoring system that clearly shows why a setup has high conviction.
- Aggressively reduce noise in the output.
- Build for macro timeframes (not short-term/day trading).
- Serve primarily as a high-quality idea generation engine with a smart, persistent watchlist.

**Supporting Capability**  
Deep fundamental research acts as a secondary validation layer that activates only on the highest-conviction setups to evaluate company quality, management track record, partnerships, financial health, and key risks.

### Project Overview
This is an intelligent stock scanner focused on macro thematic setups. It combines technical pattern detection with narrative/theme intelligence and optional deep research to surface asymmetric opportunities.

We are now using **Grok Build** as the primary coding agent.

### How to Run
- Dashboard: `streamlit run src/dashboard/app.py`
- Historical data download: `python -m src.data.historical download`
    (full thematic sectors now include Batteries/Quantum/Defense/Nuclear for doc parity)
- Backtest: `python -m src.backtest.run`
- Modal remote operations (after `modal token new`):
    - `modal run -m src.modal_app.app::download --symbols NVDA,PLTR`
    - Sector-based: `modal run -m src.modal_app.app::download --sector AI --sector Chips --days 5500 --force` (now supported)
    - Clear all historical data on Modal (start fresh with a new stock list): `modal run -m src.modal_app.app::clear_historical`
    - Local equivalent (respects STOCK_DATA_ROOT): `python -m src.data.historical clear`

### X (Twitter) Posts in the Narrative Layer

The narrative scorer can optionally incorporate recent posts from a curated list of high-quality X accounts (real-time market news, macro voices, AI/tech infrastructure, technical analysis, and sector-specific handles).

- These posts act as a **moderate popularity / momentum booster**, not the primary signal.
- When an X post from the list mentions a ticker **and** aligns with recognized themes or catalysts, it receives a small additional weight in the composite narrative score.
- The feature is **completely optional**. If no `X_BEARER_TOKEN` (or `TWITTER_BEARER_TOKEN`) is present in the environment, the X source is silently skipped and the rest of the system behaves exactly as before.
- Posts are fetched via the X API v2 recent search endpoint, cached daily alongside other news, and deduplicated with Polygon/yfinance items.

**Curated account list**: Defined in `src/narrative/sources/x_accounts.py`.

**Adding or removing accounts**: Edit the `HIGH_QUALITY_X_ACCOUNTS` list in that file. Keep the list focused on high-signal, relatively low-noise accounts. Changes take effect on the next narrative scoring run.

To enable: set `X_BEARER_TOKEN=...` (or `TWITTER_BEARER_TOKEN`) in your `.env`. The source will be automatically included via `default_sources()`.

### Date/Time Handling

All date and "today" calculations in the project (cache freshness, incremental downloads, news cache keys, historical data logic, etc.) must go through the centralized UTC helpers in `src/utils/time.py`:

- `get_current_utc_datetime()`
- `get_current_utc_date()`

This eliminates flakiness from `date.today()` (local timezone) vs `datetime.now(tz=timezone.utc)` and ensures consistent behavior on developer machines, CI, and cloud environments such as GitHub Codespaces.

### Active Specifications

- [Screener Refactor](docs/specs/SCREENER_REFACTOR.md)
- [Dashboard Spec](docs/specs/DASHBOARD_SPEC.md)
