"""Trigger a Modal-side briefing generation from Codespaces / your laptop.

The actual LLM calls + DB write + Markdown write all happen inside the
``transcript-ingestion`` Modal app. This script just submits the job
and prints the returned summary.

Examples::

    # Today's briefing (24h lookback)
    python scripts/generate_briefing.py

    # Specific date
    python scripts/generate_briefing.py --date 2026-05-23

    # Wider window — useful right after a backfill to catch everything new
    python scripts/generate_briefing.py --lookback-hours 720

    # Preview only; does NOT write to the volume or DB
    python scripts/generate_briefing.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys

import modal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--date", type=str, default=None,
                        help="ISO date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--lookback-hours", type=int, default=24,
                        help="Pick up episodes ingested in the last N hours (default 24)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute the briefing but do not write to DB or volume")
    args = parser.parse_args(argv)

    try:
        fn = modal.Function.from_name("transcript-ingestion", "manual_briefing")
    except Exception as exc:
        print(f"error: cannot find Modal function transcript-ingestion/manual_briefing:\n  {exc}",
              file=sys.stderr)
        print("hint: run `modal deploy src/narrative/sources/youtube/modal_app.py` first",
              file=sys.stderr)
        return 1

    print(
        f"invoking transcript-ingestion/manual_briefing "
        f"(date={args.date or 'today'}, lookback_hours={args.lookback_hours}, "
        f"dry_run={args.dry_run})...",
        file=sys.stderr,
    )
    result = fn.remote(
        briefing_date=args.date,
        lookback_hours=args.lookback_hours,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
