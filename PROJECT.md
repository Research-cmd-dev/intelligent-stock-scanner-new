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
- Backtest: `python -m src.backtest.run`