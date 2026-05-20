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

The terminal support guide explicitly lists VS Code as one of the detected terminals, but notes it uses the simpler BEL protocol for other features and has no special OSC 52 guarantees.

## Working Workarounds

### 1. File-based bypass (Best & Recommended)
This workspace already has a dedicated `grok-outputs/` folder for exactly this reason.

**How to use:**
- Ask Grok: "save this to a file", "write the full output to `grok-outputs/whatever.md`", etc.
- Open the resulting `.md` file using the normal VS Code editor (left sidebar / file explorer).
- Copy/paste from the editor — 100% reliable clipboard.

See [grok-outputs/README.md](grok-outputs/README.md) for the original setup notes.

### 2. VS Code Terminal Native Copy
Even when Grok's internal copy fails, VS Code's own terminal copy often works:

- Mouse-select the desired text in the terminal.
- Right-click → **Copy** (or "Copy Selection").
- Keyboard shortcut: `Ctrl+Shift+C` (Windows/Linux) or `Cmd+Shift+C` (Mac).

This completely bypasses Grok's clipboard layer and uses VS Code's terminal integration directly.

### 3. Switch Terminal Emulators (if possible)
The following terminals handle OSC 52 reliably even over SSH/remote connections:
- Ghostty
- WezTerm
- Kitty
- iTerm2 (with "Applications in terminal may access clipboard" enabled in Settings → General → Selection)

Apple Terminal is particularly bad with OSC 52 over SSH.

### 4. tmux Configuration (only relevant if you enter tmux)
If you ever run Grok inside tmux, add this to `~/.tmux.conf`:

```tmux
set -g set-clipboard on
set -g allow-passthrough on
```

Then reload:
```bash
tmux source-file ~/.tmux.conf
```

## Related Slash Commands & Flags
- `/terminal-check` — live diagnostic (what produced the line above)
- `/terminal-setup` — related configuration helper
- Launch flag: `grok --no-alt-screen` (forces inline mode, sometimes helps with certain terminal issues)

## Source References
- Built-in guide: `/home/codespace/.grok/docs/user-guide/20-terminal-support.md`
- This workspace's prior workaround setup: `grok-outputs/README.md`

---

**Bottom line:** In VS Code + Codespaces, treat the terminal as a display-only surface for Grok. Use the `grok-outputs/` file method or VS Code's own `Ctrl+Shift+C` for anything you need to keep. The TUI's fancy OSC 52 path will keep trying, but it won't succeed in this specific environment.

---

We need to fix date/timezone inconsistencies across the codebase.

The current mix of `datetime.now(tz=timezone.utc)` and `date.today()` causes unreliable behavior (especially in cloud environments like Codespaces).

Please do the following:

1. Create a small utility function in `src/utils/time.py` (or a suitable location) with the following functions:
   - `get_current_utc_datetime()` → returns current datetime in UTC
   - `get_current_utc_date()` → returns current date in UTC

2. Update the following files to use the new utility functions instead of direct date/time calls:
   - `src/data/historical.py`
   - `src/data/polygon_client.py`
   - `src/data/fetcher.py`
   - Any yfinance-related date handling

3. Fix the failing test in `tests/test_research_llm.py`:
   - Remove or update the hardcoded date assertion (`assert "2026-05-19" in user_text`).

4. Ensure that cache freshness checks, incremental download logic, and news cache keys use the new UTC-based functions.

5. Add a short comment in PROJECT.md under a new "Date/Time Handling" section explaining that we standardize on UTC for reliability across environments.

Keep the changes minimal and focused. Prioritize consistency and robustness.

Begin now.