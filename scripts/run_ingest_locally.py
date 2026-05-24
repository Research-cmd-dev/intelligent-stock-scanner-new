"""Trigger an ad-hoc ingestion run from Codespaces / your laptop.

This script runs LOCALLY but executes the actual work REMOTELY on Modal.
You're just invoking the Modal function — discovery, fetching, Whisper,
and DB writes all happen in the cloud container.

Usage::

    python scripts/run_ingest_locally.py --podcast-only --limit 3 --dry-run
    python scripts/run_ingest_locally.py --limit 5
    python scripts/run_ingest_locally.py --episode-ids vid1,vid2

The script blocks until the remote function returns, then prints the
summary dict. For live logs while it runs, watch the Modal dashboard.
"""

from __future__ import annotations

import argparse
import json
import sys

import modal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--episode-ids", type=str, default=None,
                        help="Comma-separated list of episode_ids")
    parser.add_argument("--podcast-only", action="store_true")
    parser.add_argument("--youtube-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--whisper-model", type=str, default="small.en")
    args = parser.parse_args(argv)

    if args.podcast_only and args.youtube_only:
        print("error: --podcast-only and --youtube-only are mutually exclusive",
              file=sys.stderr)
        return 2

    try:
        fn = modal.Function.from_name("transcript-ingestion", "manual_ingest")
    except Exception as exc:
        print(f"error: cannot find Modal function transcript-ingestion/manual_ingest:\n  {exc}",
              file=sys.stderr)
        print("hint: run `modal deploy src/narrative/sources/youtube/modal_app.py` first",
              file=sys.stderr)
        return 1

    episode_ids = (
        [x.strip() for x in args.episode_ids.split(",") if x.strip()]
        if args.episode_ids
        else None
    )

    print("invoking transcript-ingestion/manual_ingest on Modal...", file=sys.stderr)
    result = fn.remote(
        limit=args.limit,
        episode_ids=episode_ids,
        podcast_only=args.podcast_only,
        youtube_only=args.youtube_only,
        dry_run=args.dry_run,
        retry_failed=args.retry_failed,
        lookback_days=args.lookback_days,
        whisper_model_name=args.whisper_model,
    )

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
