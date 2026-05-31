# CLAUDE.md — Operating Protocol for the Intelligent Stock Scanner

Read this fully at the start of every session. It is the index and protocol;
content lives in the Memory Bank files below.

## Memory Bank — read these, in this order, at session start
1. projectContext.md   — mission, architecture, tech stack. Stable; rarely changes.
2. conventions.md      — coding style, project-specific patterns. Stable.
3. decisionLog.md      — why past choices were made. READ BEFORE proposing changes
                         that might reverse a deliberate decision.
4. activeContext.md    — the single task currently in flight and its state.
5. progress.md         — append-only ledger of completed work.
Also run `git log --oneline -15` and check NOTES.md (gitignored carry-forward).

## File responsibilities — keep sharp, never let them overlap
- projectContext.md : stable truth about WHAT this is and how it's built.
- conventions.md    : stable rules about HOW we write code here.
- decisionLog.md    : append-only. WHY we chose X over Y. Never delete entries.
- activeContext.md  : the ONE current task. Overwrite freely. Not a history.
- progress.md       : append-only list of COMPLETED items. Only grows.
If a fact could live in two files, it belongs in the more stable one.

## Plan before acting
For any non-trivial task (more than a single small edit):
1. Produce a plan and show it to me BEFORE touching files. Wait for approval.
2. For large/multi-step work, write the plan into activeContext.md as a
   checklist and tick items off, so a resumed session knows where it stopped.
Correcting a plan is cheap; correcting an hour of wrong execution is not.

## Verification loop — how you know a task is DONE
After ANY change, run:
    make test            # pytest -q — full suite, hermetic, no network/keys
For scanner-pipeline changes, the end-to-end check is the synthetic-fixture path:
    pytest tests/test_scanner.py tests/test_scanner_narrative.py tests/test_scanner_research.py -q
These inject synthetic OHLCV at the fetch_many seam and drive the full
universe→fetch→indicators→detect→rank→ScanReport path with zero network.
NOTE: `make scan` is a stale no-op (no __main__ in scanner.py) — do NOT use it
as a verification step. A live real-data run goes through the dashboard (make run).

A task is NOT complete until the suite passes. If it fails, fix and re-run.
Do not report done on the basis of edits alone — only on passing checks.

## Session-end ritual — flush memory to disk before stopping
1. Update activeContext.md to the true current state.
2. Append completed work to progress.md.
3. Append any real architectural/design decision (and reasoning) to decisionLog.md.
4. Commit with a clear, specific message describing what changed and why.
Anything not written to a file or commit is LOST when the session ends.

## Autonomy guardrails — non-negotiable
- PUBLIC repo. Never write secrets/keys/tokens into any TRACKED file. Secrets
  live only in .env (gitignored). Note NOTES.md is gitignored — safe for
  carry-forward, but still never put live credentials in it.
- The scanning / narrative / briefing / research layers are safe to work on
  autonomously.
- Modal jobs run real compute and cost money — confirm scope before launching
  large Modal runs (downloads/backtests over the full ~560-ticker universe).
- There is NO live broker / order execution in this project. If any task implies
  wiring up real trading, stop and ask.
- Match autonomy to reversibility: refactors are reversible; anything with
  irreversible external effect needs my sign-off.
