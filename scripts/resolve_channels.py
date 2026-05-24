"""Resolve YouTube ``@handle`` URLs to canonical ``UC...`` channel IDs.

YouTube's RSS feed endpoint (``/feeds/videos.xml?channel_id=UC...``) takes
the canonical channel ID, not the ``@handle``. This one-shot script reads
``config/channels.yaml``, fetches each channel's public page, scrapes the
channel ID via several fallback regexes, and writes the result back in place.

Idempotent by design: entries with a non-null ``channel_id`` are skipped, so
re-running only attempts the unresolved ones. Bad handles log a warning
and continue — the script never raises on a single failure.

Run from the repo root:

    python scripts/resolve_channels.py

No HTTP calls happen in the application hot path; only here.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import requests
import yaml

log = logging.getLogger("resolve_channels")

DEFAULT_CONFIG = Path("config/channels.yaml")

# Three fallback patterns — YouTube's HTML has shuffled them around across
# the years. We try the cheapest first (meta tag), then the canonical link,
# then the embedded JSON blob.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'<meta\s+itemprop="(?:channelId|identifier)"\s+content="(UC[\w-]{22})"'),
    re.compile(r'<link\s+rel="canonical"\s+href="https?://www\.youtube\.com/channel/(UC[\w-]{22})"'),
    re.compile(r'"channelId"\s*:\s*"(UC[\w-]{22})"'),
    re.compile(r'"externalId"\s*:\s*"(UC[\w-]{22})"'),
    re.compile(r'channel_id=(UC[\w-]{22})'),  # RSS-alternate <link> on /@handle
)

# Public channel pages return the same HTML to most browsers — a UA still
# matters because some Googlebot-restricted variants ship a stripped page.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def resolve_channel_id(handle_url: str, *, timeout: int = 15) -> str | None:
    """Fetch ``handle_url`` and return its UC... channel ID, or None."""
    try:
        resp = requests.get(
            handle_url, headers=_HEADERS, timeout=timeout, allow_redirects=True
        )
    except requests.RequestException as exc:
        log.warning("fetch failed: %s (%s)", handle_url, exc)
        return None

    if resp.status_code != 200:
        log.warning("fetch HTTP %s: %s", resp.status_code, handle_url)
        return None

    html = resp.text
    for pattern in _PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1)
    return None


def resolve_all(config_path: Path, *, sleep_s: float = 0.5) -> tuple[int, int, int, list[str]]:
    """Resolve every unresolved channel in ``config_path``, write back, return counts.

    Returns ``(resolved, already, failed, failed_names)``.
    """
    data = yaml.safe_load(config_path.read_text())
    if not isinstance(data, dict) or "channels" not in data:
        raise SystemExit(f"{config_path} missing top-level 'channels:' list")

    resolved = 0
    already = 0
    failed: list[str] = []

    for entry in data["channels"]:
        name = entry.get("name", "<unnamed>")
        if entry.get("channel_id"):
            already += 1
            continue
        handle = entry.get("handle")
        if not handle:
            log.warning("no handle for %s — skip", name)
            failed.append(name)
            continue

        cid = resolve_channel_id(handle)
        if cid:
            entry["channel_id"] = cid
            resolved += 1
            log.info("resolved %s -> %s", name, cid)
        else:
            log.warning("could not resolve %s (%s)", name, handle)
            failed.append(name)

        time.sleep(sleep_s)

    # Atomic write: dump to a sibling .tmp, then rename.
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)
    )
    tmp.replace(config_path)

    return resolved, already, failed.__len__(), failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to channels.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to sleep between HTTP requests (default: %(default)s)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Log DEBUG-level details"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.config.exists():
        log.error("config not found: %s", args.config)
        return 2

    resolved, already, failed_count, failed_names = resolve_all(
        args.config, sleep_s=args.sleep
    )

    log.info("done: resolved=%d already=%d failed=%d", resolved, already, failed_count)
    if failed_names:
        log.info("failed: %s", ", ".join(failed_names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
