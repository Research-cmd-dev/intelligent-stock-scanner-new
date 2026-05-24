"""One-shot verifier for ``podcast_rss`` URLs in ``config/channels.yaml``.

For each channel with a non-null ``podcast_rss``, fetch the feed and
report ``OK (N entries, latest <date>)`` or ``FAIL (reason)``. The script
does NOT mutate the YAML — duds get listed at the end and the user
decides whether to null them out or replace with a corrected URL.

Run from the repo root::

    python scripts/verify_podcast_feeds.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import yaml

log = logging.getLogger("verify_podcast_feeds")

DEFAULT_CONFIG = Path("config/channels.yaml")
_UA = "Mozilla/5.0 (compatible; ScannerBot/0.1; +intelligent-stock-scanner)"


def _fetch(url: str, *, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return r.read()


def _latest_pub(feed: feedparser.FeedParserDict) -> datetime | None:
    latest: datetime | None = None
    for e in feed.entries:
        tup = e.get("published_parsed") or e.get("updated_parsed")
        if tup is None:
            continue
        dt = datetime(tup[0], tup[1], tup[2], tup[3], tup[4], tup[5], tzinfo=timezone.utc)
        if latest is None or dt > latest:
            latest = dt
    return latest


def _check(name: str, url: str) -> tuple[bool, str]:
    """Return (ok, message)."""
    try:
        body = _fetch(url)
    except Exception as exc:
        return False, f"fetch failed ({type(exc).__name__}): {exc}"
    feed = feedparser.parse(body)
    if feed.bozo and not feed.entries:
        return False, f"parse failed: {feed.bozo_exception}"
    n = len(feed.entries)
    if n == 0:
        return False, "0 entries"
    latest = _latest_pub(feed)
    latest_str = latest.date().isoformat() if latest else "unknown"
    return True, f"{n} entries, latest {latest_str}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.config.exists():
        log.error("config not found: %s", args.config)
        return 2
    data: dict[str, Any] = yaml.safe_load(args.config.read_text()) or {}
    channels = data.get("channels") or []

    ok_names: list[str] = []
    fail_names: list[tuple[str, str, str]] = []  # (name, url, reason)
    no_url = 0

    for ch in channels:
        name = ch.get("name") or "<unnamed>"
        url = ch.get("podcast_rss")
        if not url:
            no_url += 1
            continue
        ok, msg = _check(name, url)
        if ok:
            ok_names.append(name)
            print(f"OK    {name:32s}  {msg}")
        else:
            fail_names.append((name, url, msg))
            print(f"FAIL  {name:32s}  {msg}")

    total_with_url = len(ok_names) + len(fail_names)
    print()
    print(f"summary: {len(ok_names)}/{total_with_url} ok, {no_url} channels have no podcast_rss")
    if fail_names:
        print()
        print("failures:")
        for name, url, reason in fail_names:
            print(f"  - {name}: {url}  ({reason})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
