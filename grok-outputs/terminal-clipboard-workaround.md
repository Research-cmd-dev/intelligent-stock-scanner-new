# Grok TUI Terminal Clipboard Workaround — VS Code / Codespaces

**Date:** 2026-05-20  
**Environment:** GitHub Codespaces (Linux) + VS Code integrated terminal

## Problem
"I still can't copy the text in here"

Selecting or triggering copy inside the Grok Build full-screen TUI does not put text on the system clipboard.

## `/terminal-check` Diagnosis
```
Terminal VS Code, Native active, tmux buffer off, osc 52 active
```

### What this means
- Grok correctly detected **VS Code** integrated terminal.
- It is attempting the **native** clipboard path.
- No tmux is involved (`tmux buffer off`).
- It is emitting **OSC 52** escape sequences (the standard way full-screen TUIs push to clipboard over remote connections).

Despite all three paths being active on Grok's side, the clipboard write is not reaching the user's host clipboard.

## Root Cause
VS Code's integrated terminal (especially when connected remotely via Codespaces or Remote-SSH) has unreliable / broken support for OSC 52 clipboard synchronization coming from full-screen TUIs. The "native" fallback also fails in this bridged environment. This is a well-known limitation.

## Working Workarounds

### 1. File-based bypass (Best & Recommended)
This workspace already has a dedicated `grok-outputs/` folder for exactly this reason.

**How to use:**
- Ask Grok: "save this to a file", "write the full output to `grok-outputs/whatever.md`", etc.
- Open the resulting `.md` file using the normal VS Code editor (left sidebar / file explorer).
- Copy/paste from the editor — 100% reliable clipboard.

### 2. VS Code Terminal Native Copy
Even when Grok's internal copy fails, VS Code's own terminal copy often works:

- Mouse-select the desired text in the terminal.
- Right-click → **Copy** (or "Copy Selection").
- Keyboard shortcut: `Ctrl+Shift+C` (Windows/Linux) or `Cmd+Shift+C` (Mac).

This completely bypasses Grok's clipboard layer and uses VS Code's own terminal integration directly.

### 3. Switch Terminal Emulators (if possible)
The following terminals handle OSC 52 reliably even over SSH/remote connections:
- Ghostty
- WezTerm
- Kitty
- iTerm2 (with "Applications in terminal may access clipboard" enabled)

## 2026-05-20 — Code Review + Test Execution Session

**Context**: Full repository code review performed via dedicated reviewer subagent. As part of the review, terminal commands were executed (`make test` / `pytest -q`).

**Terminal output captured**:
- Command: `python -m pytest -q --tb=no` (equivalent to `make test`)
- Result: **129 passed, 1 failed, 1 warning** (ran in ~20 seconds)
- The single failure was unrelated to new code (Modal/historical layer) but highlighted a recurring terminal/cloud date issue.

**Failure details**:
- Test: `tests/test_research_llm.py::test_request_shape_matches_prompt_caching_contract`
- Root cause: Hard-coded assertion `assert "2026-05-19" in user_text` vs. actual `datetime.now(tz=utc)` producing "2026-05-20" in the Codespace environment.

**New systemic finding surfaced during review** (Issue 4 in code review):
- Date/timezone inconsistency across the data layer:
  - `src/data/historical.py:276` uses `datetime.now(tz=timezone.utc).date()`
  - `src/data/polygon_client.py:46` uses `date.today()` (local TZ)
  - Similar patterns in `fetcher.py` and yfinance path
- This affects cache freshness, "is up-to-date" checks, incremental lookback calculations, and cache-freshness decisions — especially noticeable in cloud terminals (Codespaces) whose TZ may differ from the developer's local machine or UTC.

**Relevance to clipboard workaround**:
Long-running terminal operations (reviews, full test suites, historical downloads, backtests) frequently produce output the user wants to preserve and copy. The date/TZ fragility also means that commands run in this terminal environment can behave differently than on a local machine, making reliable capture via the editor (grok-outputs/) even more important.

**Action taken**: This section was added per the standing project memory rule to keep the workaround file current with any new terminal execution output or environment-specific behaviors discovered.

---

## 2026-05-20 (later) — X (Twitter) High-Quality Accounts Integration + UTC Date Standardization

**Context**: Implementing optional X (Twitter) posts as a narrative signal + fixing date/timezone inconsistencies.

**Terminal commands & outputs captured**:

1. Smoke test after adding X source (before token configured):

```bash
python -c "
from src.narrative.sources import default_sources, XAccountsNewsSource, HIGH_QUALITY_X_ACCOUNTS
from src.config import get_settings
print('HIGH_QUALITY_X_ACCOUNTS count:', len(HIGH_QUALITY_X_ACCOUNTS))
print('has_x (no token):', get_settings().has_x)
srcs = default_sources()
print('Default sources count (no token):', len(srcs))
print('X source class present:', any('XAccounts' in str(type(s)) for s in srcs))
print('Import successful')
"
```

**Output**:
```
HIGH_QUALITY_X_ACCOUNTS count: 16
has_x (no token): False
Default sources count (no token): 2
X source class present: False
Import successful
```

2. Final verification after adding `unusual_whales`:

```bash
python -c "
from src.narrative.sources.x_accounts import HIGH_QUALITY_X_ACCOUNTS
print('Total accounts:', len(HIGH_QUALITY_X_ACCOUNTS))
print('unusual_whales present:', 'unusual_whales' in HIGH_QUALITY_X_ACCOUNTS)
"
```

**Output**:
```
Total accounts: 17
unusual_whales present: True
```

3. Git commit and push of the X + UTC date/time work:

```bash
git status --porcelain
git add src/narrative/sources/x_accounts.py src/narrative/sources/x_news.py src/utils/time.py ...
git commit -m "Add optional X (Twitter) high-quality accounts to narrative layer + centralize UTC date/time handling"
git push
git log --oneline -3
```

**Key output**:
```
d13cfcc Add optional X (Twitter) high-quality accounts to narrative layer + centralize UTC date/time handling
c1cc9c6 Add Modal compute layer and durable historical OHLCV store
```

**Relevance to clipboard workaround**:
During active development we run many `git status`, `git add`, `git commit`, and verification Python one-liners. These outputs are frequently needed for reference (e.g. "what was the exact commit message?", "which files were changed in the X integration?"). Saving them here prevents loss when the terminal buffer is cleared.

**Action taken**: This section was appended per the standing project memory rule to keep `terminal-clipboard-workaround.md` as the living record of terminal behavior and important command outputs in this VS Code + Codespaces environment.

---

**Bottom line:** In VS Code + Codespaces, treat the terminal as a display-only surface for Grok. Use the `grok-outputs/` file method or VS Code's own `Ctrl+Shift+C` for anything you need to keep. The TUI's fancy OSC 52 path will keep trying, but it won't succeed in this specific environment.

**File is now up to date** with all terminal activity from the X integration, UTC date fixes, and the final commit + push.