# Conventions
## Time handling (hard rule)
All "today"/date logic goes through src/utils/time.py — get_current_utc_datetime()
and get_current_utc_date(). Never use date.today() or naive datetime.now().
## Testing pattern (the key seam)
Tests inject synthetic OHLCV by monkey-patching fetch_many at the scanner
boundary (see tests/synthetic.py). This exercises the full pipeline hermetically
— no network, deterministic. New pipeline work follows this seam.
## Optional dependencies
Optional layers (X, research, Modal, Telegram, Webshare) must degrade silently
when their env/keys are absent — the core must behave identically without them.
## Detectors
Patterns are registered detectors (src/scanner/patterns/, ALL_DETECTORS) returning
MatchResult; new patterns subclass the base detector and register there.
## Failure handling
A single symbol's failed fetch is dropped with a warning, never aborts the run
(coverage counters record attempted vs fetched vs matches).
## Caching / storage
Parquet via pyarrow; STOCK_DATA_ROOT controls the historical store root (Modal
sets it to the mounted /data volume).
## Style
Python 3.12, `from __future__ import annotations` at the top of every module, full
type hints. No automated formatter/linter is committed — no ruff/black/flake8/isort
config and none in requirements. mypy is used (`.mypy_cache` present) but with
default settings (no config file). Code is hand-wrapped at roughly 88 columns
(docstrings/ticker tables occasionally to ~92). Match the surrounding style;
don't introduce a formatter without a decisionLog entry.
