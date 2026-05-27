# Stock Finder Agent

A daily stock scanner that hunts for high-conviction setups in thematic sectors,
organized as 8 large-cap-led majors (AI, Chips, Energy, Space, Robotics, Bio,
Software, Misc) paired with 8 smaller-cap micro lanes (AI_micro, Chips_micro,
Energy_micro, Space_micro, Robotics_micro, Bio_micro, Software_micro, Misc_micro).
The micro lanes carry the project's quantum, nuclear, batteries, fintech, and
crypto-miner exposure. Total: ~560 unique tickers across 16 sectors.

It detects two complementary patterns:

1. **Trend Rider** — pullbacks inside established uptrends.
2. **Bottom Hunter** — rounding bottoms reclaiming long-term moving averages.

Results are narrated in plain English and displayed in a Streamlit dashboard.

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Add a Polygon API key — without it, the scanner uses yfinance
cp .env.example .env
# edit .env and set POLYGON_API_KEY=...

# 3. Launch the dashboard
streamlit run src/dashboard/app.py
```

## Dev container

Open the repo in GitHub Codespaces or VS Code Dev Containers — the
`.devcontainer/` config installs Python 3.12 and the project dependencies
automatically.

## Project layout

See `CLAUDE.md` for a description of the module layout and design decisions.

## Disclaimer

This project is for research and education. Nothing here is financial advice.
