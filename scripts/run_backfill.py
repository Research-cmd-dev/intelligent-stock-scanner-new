"""Trigger a one-shot historical backfill from Codespaces / your laptop.

Local entry point that invokes the Modal ``backfill_episodes`` function
remotely. The actual work — discovery, MP3 downloads, GPU Whisper,
SQLite writes — happens in the cloud container.

NOT scheduled. Manual only. Designed to be run during off-hours since
500 episodes can take 5-25 hours single-container.

Examples::

    # See what would happen, no GPU minutes spent
    python scripts/run_backfill.py --dry-run

    # Real run: tier-1 speakers, last 18 months, cap at 500 episodes
    python scripts/run_backfill.py

    # Wider net: tier-1 and tier-2 speakers
    python scripts/run_backfill.py --tiers 1,2

    # Tiny smoke first — verify path + cost before committing
    python scripts/run_backfill.py --max-episodes 5

    # Custom lookback
    python scripts/run_backfill.py --lookback-days 365 --max-episodes 200
"""

from __future__ import annotations

import argparse
import json
import sys

import modal

# Hard cost-safety ceiling. Edit deliberately if you really want more.
MAX_EPISODES_HARD_CAP = 1500


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--lookback-days", type=int, default=540,
                        help="Days to look back in each podcast feed (default 540 = 18 months)")
    parser.add_argument("--max-episodes", type=int, default=500,
                        help=f"Hard cap on episodes processed this run (max {MAX_EPISODES_HARD_CAP})")
    parser.add_argument("--tiers", type=str, default="1",
                        help="Comma-separated speaker tiers to include (default: 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report candidate count + cost estimate; no GPU minutes spent")
    parser.add_argument("--whisper-model", type=str, default="small.en")
    args = parser.parse_args(argv)

    if args.max_episodes <= 0:
        print("error: --max-episodes must be > 0", file=sys.stderr)
        return 2
    if args.max_episodes > MAX_EPISODES_HARD_CAP:
        print(
            f"error: --max-episodes capped at {MAX_EPISODES_HARD_CAP} for cost safety. "
            "Edit run_backfill.py to override deliberately.",
            file=sys.stderr,
        )
        return 2

    try:
        tiers = [int(t.strip()) for t in args.tiers.split(",") if t.strip()]
    except ValueError:
        print(f"error: --tiers must be a comma-separated list of integers, got {args.tiers!r}",
              file=sys.stderr)
        return 2
    if not tiers:
        print("error: --tiers cannot be empty", file=sys.stderr)
        return 2

    try:
        fn = modal.Function.from_name("transcript-ingestion", "backfill_episodes")
    except Exception as exc:
        print(f"error: cannot find Modal function transcript-ingestion/backfill_episodes:\n  {exc}",
              file=sys.stderr)
        print("hint: run `modal deploy src/narrative/sources/youtube/modal_app.py` first",
              file=sys.stderr)
        return 1

    print(
        f"invoking transcript-ingestion/backfill_episodes on Modal "
        f"(lookback={args.lookback_days}d, max={args.max_episodes}, tiers={tiers}, "
        f"dry_run={args.dry_run})...",
        file=sys.stderr,
    )
    result = fn.remote(
        lookback_days=args.lookback_days,
        max_episodes=args.max_episodes,
        speaker_tiers=tiers,
        dry_run=args.dry_run,
        whisper_model_name=args.whisper_model,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
