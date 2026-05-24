# Transcript ingestion

The transcript pipeline lives in `src/narrative/sources/youtube/` and is
driven by `scripts/ingest_transcripts.py`. It pulls audio from podcast
RSS feeds and/or YouTube and writes chunked transcripts into
`data/narrative/transcripts.db`, then optionally syncs the SQLite file
to the Modal `stock_data` volume.

---

## Architecture (Phase 3)

Four fetch paths, picked per candidate by source:

| Path | Trigger | IP-block risk |
|---|---|---|
| 0 — podcast MP3 + Whisper | `candidate.source == "podcast"` | None (podcast hosts) |
| 1 — `youtube-transcript-api` | `source == "youtube"` first try | High — needs residential IP |
| 2 — `yt-dlp --write-auto-sub` | Path 1 failed | High — needs residential IP |
| 3 — `yt-dlp --extract-audio` + Whisper | Paths 1+2 both failed AND Whisper available | High — needs residential IP |

Roughly 80% of the watched shows publish as standard podcasts; those
flow through Path 0 from any IP. The remaining 20% (company channels,
video-first shows like TBPN) go through Paths 1–3 and need residential
egress.

---

## Why this script must run locally, not on Modal

YouTube actively blocks the cloud-provider IP ranges that AWS/GCP/Azure
publish — that includes Modal and GitHub Codespaces. Path 0 works fine
from anywhere, but to cover the YouTube-only fraction the script needs
a residential IP. The simplest answer is one machine that runs the
whole pipeline.

The only artifact that crosses to Modal is `transcripts.db` via
`modal volume put`; transcripts themselves never leave the local box.

---

## Install (local machine)

```bash
pip install -r requirements.txt
pip install -r requirements-local.txt   # whisper + yt-dlp + transcript-api
```

`yt-dlp` is also a CLI binary; on macOS `brew install yt-dlp`, on
Debian/Ubuntu `apt install yt-dlp` (or `pip install yt-dlp` brings the
CLI too).

`faster-whisper` will download model weights on first run. The default
is `small.en` (~500 MB); other options: `base.en` (faster), `medium.en`
(slower, better accuracy).

---

## CLI

```
python scripts/ingest_transcripts.py [options]
```

| Flag | Effect |
|---|---|
| `--limit N` | Process at most N candidates this run |
| `--episode-ids ID1,ID2` | Process only these episode_ids (must still appear in discovery) |
| `--podcast-only` | Skip YouTube paths entirely |
| `--youtube-only` | Skip podcast paths entirely |
| `--no-whisper` | Disable Whisper (Path 0 candidates get skipped; Path 3 unavailable) |
| `--whisper-model NAME` | Override default `small.en` |
| `--dry-run` | List candidates with `[would process]` markers; no fetches |
| `--no-modal-sync` | Skip the post-run `modal volume put` |
| `--retry-failed` | Re-try candidates whose last attempt failed >7d ago |
| `--db PATH` | SQLite path (default `data/narrative/transcripts.db`) |
| `--lookback-days N` | Podcast feed lookback (default 30) |

---

## Scheduling

### macOS / Linux: cron

```
# m h dom mon dow  command
17 4 * * * cd /path/to/repo && /path/to/venv/bin/python scripts/ingest_transcripts.py >> ~/.log/ingest.log 2>&1
```

The laptop must be awake at the scheduled time. On macOS, either run
during waking hours or wrap with `caffeinate -i`, or schedule a wake
via `pmset repeat wakeorpoweron MTWRFSU 04:15:00`.

### macOS launchd alternative

`~/Library/LaunchAgents/com.local.scanner-ingest.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
                       "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.local.scanner-ingest</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/venv/bin/python</string>
    <string>/path/to/repo/scripts/ingest_transcripts.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>17</integer></dict>
  <key>WorkingDirectory</key><string>/path/to/repo</string>
  <key>StandardOutPath</key><string>/path/to/repo/logs/ingest.out</string>
  <key>StandardErrorPath</key><string>/path/to/repo/logs/ingest.err</string>
</dict>
</plist>
```

Load: `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.local.scanner-ingest.plist`.

### Windows: Task Scheduler

Create a Basic Task → Daily → 04:17 → Start a program:
`C:\path\to\venv\Scripts\python.exe scripts\ingest_transcripts.py`
(Start in: `C:\path\to\repo`)

### Upgrade path: self-hosted GitHub Actions runner

Register the laptop as a self-hosted runner, then enable the
(currently disabled) `.github/workflows/ingest.yml` workflow for cron +
manual triggering via the GitHub UI. Jobs run on the user's hardware so
the residential IP is preserved.

---

## Operations

### What's in the DB?

```bash
sqlite3 data/narrative/transcripts.db
> .schema episodes
> SELECT source, COUNT(*) FROM episodes GROUP BY source;
> SELECT episode_id, text FROM chunks_fts WHERE chunks_fts MATCH 'inference' LIMIT 5;
```

### Which episodes failed?

```bash
sqlite3 data/narrative/transcripts.db \
  "SELECT episode_id, attempted_at, error FROM ingest_log WHERE status='failed' ORDER BY attempted_at DESC LIMIT 20;"
```

### Force a re-ingest of one episode

```bash
sqlite3 data/narrative/transcripts.db "DELETE FROM episodes WHERE episode_id='vid12345678';"
python scripts/ingest_transcripts.py --episode-ids vid12345678
```

### Verify a podcast feed URL

```bash
python scripts/verify_podcast_feeds.py
```

---

## Stale podcast URLs

Some of the starter URLs in `config/channels.yaml` may go 404 over time
as shows change hosting platforms. When that happens:

1. Run `verify_podcast_feeds.py` to see which feeds are dead.
2. Find the show on Apple Podcasts → copy the RSS URL from the share
   menu, or search for `"<show name> RSS feed"`.
3. Patch the new URL into the channel's `podcast_rss` field in
   `config/channels.yaml` and re-verify.

The discovery layer treats a missing/null `podcast_rss` as "fall through
to YouTube" — so a dead podcast URL never blocks discovery, just slows
it down.
