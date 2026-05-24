"""Four-path transcript fetcher with explicit dispatch.

Path 0 — podcast MP3 + Whisper           (no IP-block surface)
Path 1 — youtube-transcript-api          (residential IP required)
Path 2 — yt-dlp captions (VTT)           (residential IP preferred)
Path 3 — yt-dlp audio + Whisper          (residential IP preferred)

The dispatcher routes by ``candidate.source``:

* ``"podcast"`` → Path 0 only. Whisper is required for this path because
  podcasts don't ship machine-readable transcripts.
* ``"youtube"`` → Path 1, then Path 2; on either succeeding, return. If
  both raise :class:`TranscriptUnavailable` and a Whisper model is
  available, fall through to Path 3.

Heavy dependencies (faster_whisper, youtube_transcript_api) are
*try-imported* at module load so this module can be imported anywhere
(Codespaces, Modal, CI) without the local-only deps installed. The
binding is ``None`` when the import fails; the path raises
``TranscriptUnavailable`` if asked to run without its backend.

``yt-dlp`` is invoked via ``subprocess`` rather than as a library, so no
import is needed — only the binary on ``PATH``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chunker import Segment
from .discovery import VideoCandidate

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; ScannerBot/0.1; +intelligent-stock-scanner)"

# Webshare residential proxy endpoint — supported by both
# youtube-transcript-api (via its proxy_config) and yt-dlp (via --proxy).
# Empty credentials → no proxy used → YouTube paths skip with a clear reason.
_PROXY_HOST = "p.webshare.io:80"


# --------------------------------------------------------------------- #
# Lazy backend bindings                                                 #
# --------------------------------------------------------------------- #
# Try-imported at module load so the test suite (and any non-local
# environment) can import this module cleanly. Tests patch these names
# via ``monkeypatch.setattr``.

try:  # pragma: no cover - exercised via tests with monkeypatch
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore[import-not-found]
except Exception:  # broad: package may be missing OR ImportError variant
    YouTubeTranscriptApi = None  # type: ignore[assignment, misc]


# --------------------------------------------------------------------- #
# Proxy plumbing (Webshare residential)                                 #
# --------------------------------------------------------------------- #


def _proxy_credentials() -> tuple[str, str] | None:
    user = os.getenv("WEBSHARE_PROXY_USERNAME") or ""
    pwd = os.getenv("WEBSHARE_PROXY_PASSWORD") or ""
    if user and pwd:
        return user, pwd
    return None


def has_proxy() -> bool:
    """True when both Webshare env vars are set and non-empty.

    Public so the driver can decide whether to allow ``--youtube-only``.
    """
    return _proxy_credentials() is not None


def _build_transcript_api() -> Any:
    """Construct ``YouTubeTranscriptApi`` with Webshare proxy when configured.

    Returns ``None`` if the library isn't importable. Falls back to a
    no-proxy instance if neither ``WebshareProxyConfig`` nor
    ``GenericProxyConfig`` is available in the installed library version.
    """
    if YouTubeTranscriptApi is None:
        return None
    creds = _proxy_credentials()
    if creds is None:
        return YouTubeTranscriptApi()
    user, pwd = creds
    try:
        from youtube_transcript_api.proxies import WebshareProxyConfig  # type: ignore[import-not-found]
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=user, proxy_password=pwd,
            )
        )
    except ImportError:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig  # type: ignore[import-not-found]
            proxy_url = f"http://{user}:{pwd}@{_PROXY_HOST}"
            return YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(
                    http_url=proxy_url, https_url=proxy_url,
                )
            )
        except ImportError:
            log.warning("youtube-transcript-api lacks proxy support; running direct")
            return YouTubeTranscriptApi()


def _yt_dlp_proxy_args() -> list[str]:
    """Argv tail for yt-dlp's ``--proxy`` flag, or empty list if unset."""
    creds = _proxy_credentials()
    if creds is None:
        return []
    user, pwd = creds
    return ["--proxy", f"http://{user}:{pwd}@{_PROXY_HOST}"]


# --------------------------------------------------------------------- #
# Public types                                                          #
# --------------------------------------------------------------------- #


class TranscriptUnavailable(Exception):
    """Raised when a fetch path cannot produce a transcript.

    The dispatcher catches this exception to fall through to the next
    path. Callers above the dispatcher get this raised if every path
    fails.
    """


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    segments: tuple[Segment, ...]
    source_method: str
    duration_s: float | None


# --------------------------------------------------------------------- #
# Path 0 — podcast MP3 + Whisper                                        #
# --------------------------------------------------------------------- #


def fetch_via_podcast_audio(
    candidate: VideoCandidate,
    *,
    workdir: Path,
    whisper_model: Any,
) -> TranscriptResult:
    if not candidate.audio_url:
        raise TranscriptUnavailable(
            f"no audio_url on candidate {candidate.episode_guid}"
        )
    workdir.mkdir(parents=True, exist_ok=True)
    audio_path = workdir / f"{candidate.video_id}.mp3"
    try:
        _download(candidate.audio_url, audio_path)
        segments, duration = _transcribe(whisper_model, audio_path)
    finally:
        audio_path.unlink(missing_ok=True)
    return TranscriptResult(
        segments=tuple(segments),
        source_method="podcast_rss",
        duration_s=duration,
    )


# --------------------------------------------------------------------- #
# Path 1 — youtube-transcript-api                                       #
# --------------------------------------------------------------------- #


def fetch_via_transcript_api(video_id: str) -> TranscriptResult:
    api = _build_transcript_api()
    if api is None:
        raise TranscriptUnavailable("youtube-transcript-api not installed")
    try:
        fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
    except Exception as e:  # broad — disabled / blocked / unavailable / IP-blocked
        raise TranscriptUnavailable(f"{type(e).__name__}: {e}") from e

    segments: list[Segment] = []
    for snippet in fetched:
        start = float(getattr(snippet, "start", 0.0) or 0.0)
        duration = float(getattr(snippet, "duration", 0.0) or 0.0)
        text = (getattr(snippet, "text", "") or "").strip()
        if text:
            segments.append(Segment(start_s=start, end_s=start + duration, text=text))

    if not segments:
        raise TranscriptUnavailable("transcript_api returned 0 segments")

    return TranscriptResult(
        segments=tuple(segments),
        source_method="transcript_api",
        duration_s=segments[-1].end_s if segments else None,
    )


# --------------------------------------------------------------------- #
# Path 2 — yt-dlp captions                                              #
# --------------------------------------------------------------------- #


def fetch_via_yt_dlp_subs(video_id: str, *, workdir: Path) -> TranscriptResult:
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        *_yt_dlp_proxy_args(),
        "--skip-download",
        "--write-auto-sub",
        "--write-subs",
        "--sub-langs", "en.*",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "-o", f"{workdir}/%(id)s.%(ext)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as e:
        raise TranscriptUnavailable(f"yt-dlp subs failed: {e}") from e

    vtt_files = sorted(workdir.glob(f"{video_id}*.vtt"))
    if not vtt_files:
        raise TranscriptUnavailable("yt-dlp produced no VTT file")
    try:
        segments = parse_vtt(vtt_files[0].read_text(encoding="utf-8"))
    finally:
        for f in vtt_files:
            f.unlink(missing_ok=True)

    if not segments:
        raise TranscriptUnavailable("VTT parsed to 0 segments")
    return TranscriptResult(
        segments=tuple(segments),
        source_method="yt_dlp_subs",
        duration_s=segments[-1].end_s,
    )


# --------------------------------------------------------------------- #
# Path 3 — yt-dlp audio + Whisper                                       #
# --------------------------------------------------------------------- #


def fetch_via_whisper(
    video_id: str, *, workdir: Path, whisper_model: Any
) -> TranscriptResult:
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        *_yt_dlp_proxy_args(),
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "-o", f"{workdir}/%(id)s.%(ext)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as e:
        raise TranscriptUnavailable(f"yt-dlp audio failed: {e}") from e

    wav_files = sorted(workdir.glob(f"{video_id}*.wav"))
    if not wav_files:
        raise TranscriptUnavailable("yt-dlp produced no WAV file")
    try:
        segments, duration = _transcribe(whisper_model, wav_files[0])
    finally:
        for f in wav_files:
            f.unlink(missing_ok=True)

    if not segments:
        raise TranscriptUnavailable("whisper produced 0 segments")
    return TranscriptResult(
        segments=tuple(segments),
        source_method="whisper",
        duration_s=duration,
    )


# --------------------------------------------------------------------- #
# Dispatcher                                                            #
# --------------------------------------------------------------------- #


def fetch_transcript(
    candidate: VideoCandidate,
    *,
    workdir: Path,
    whisper_model: Any,
    allow_youtube_paths: bool = True,
) -> TranscriptResult:
    """Route to the right path based on ``candidate.source``.

    Podcast candidates never touch YouTube. YouTube candidates try the
    cheap caption paths first; only fall through to Whisper when both
    fail AND a model was provided.
    """
    if candidate.source == "podcast":
        if whisper_model is None:
            raise TranscriptUnavailable("podcast path requires a whisper model")
        return fetch_via_podcast_audio(
            candidate, workdir=workdir, whisper_model=whisper_model,
        )

    if not allow_youtube_paths:
        raise TranscriptUnavailable("youtube paths disabled")
    if not has_proxy():
        # On Modal we always hit YouTube through a residential proxy. Without
        # one, all three YouTube paths will get IP-blocked; surface that as
        # one clean reason rather than three sequential network failures.
        raise TranscriptUnavailable("no_proxy_configured")

    # Path 1 → Path 2 → (Path 3 if whisper available)
    cheap_paths: list[tuple[str, Any]] = [
        ("transcript_api", lambda: fetch_via_transcript_api(candidate.video_id)),
        ("yt_dlp_subs",
         lambda: fetch_via_yt_dlp_subs(candidate.video_id, workdir=workdir)),
    ]
    for name, path_fn in cheap_paths:
        try:
            return path_fn()
        except TranscriptUnavailable as e:
            log.info("path %s failed for %s: %s", name, candidate.video_id, e)

    if whisper_model is None:
        raise TranscriptUnavailable(
            "captions unavailable and whisper not provided"
        )
    return fetch_via_whisper(
        candidate.video_id, workdir=workdir, whisper_model=whisper_model,
    )


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #


def _download(url: str, dest: Path, *, timeout: int = 600) -> None:
    """Plain HTTP GET of ``url`` to ``dest``. 1 MB chunks."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:  # noqa: S310
        shutil.copyfileobj(r, f, length=1 << 20)


def _transcribe(model: Any, audio_path: Path) -> tuple[list[Segment], float | None]:
    """Run faster-whisper transcription. Caller supplies the model.

    Wrapped so the rest of the module doesn't reach into faster-whisper's
    Segment/Info shapes directly; tests can swap ``model`` with anything
    that quacks like a WhisperModel.
    """
    segments_iter, info = model.transcribe(str(audio_path))
    segments = [
        Segment(
            start_s=float(s.start),
            end_s=float(s.end),
            text=(s.text or "").strip(),
        )
        for s in segments_iter
        if (s.text or "").strip()
    ]
    return segments, getattr(info, "duration", None)


def parse_vtt(text: str) -> list[Segment]:
    """Bare-bones WebVTT parser.

    Handles standard cue timing lines (``HH:MM:SS.mmm --> HH:MM:SS.mmm``)
    and the common YouTube auto-caption shape (``MM:SS.mmm``). Inline
    style tags like ``<c>``…``</c>`` are stripped. NOTE/STYLE/REGION
    blocks are skipped. Not a full spec implementation — sufficient for
    YouTube auto-caption output, which is the only thing we ever get
    here.
    """
    segments: list[Segment] = []
    cur_start: float | None = None
    cur_end: float | None = None
    cur_text: list[str] = []

    def _flush() -> None:
        if cur_start is None or not cur_text:
            return
        joined = " ".join(cur_text).strip()
        if joined:
            segments.append(
                Segment(start_s=cur_start, end_s=cur_end or cur_start, text=joined)
            )

    in_skip_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            in_skip_block = False
            continue
        if line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
            in_skip_block = True
            continue
        if in_skip_block:
            continue
        if " --> " in line:
            _flush()
            t1, t2_with_settings = line.split(" --> ", 1)
            t2 = t2_with_settings.split(" ", 1)[0]
            cur_start = _parse_vtt_ts(t1)
            cur_end = _parse_vtt_ts(t2)
            cur_text = []
        elif cur_start is not None:
            stripped = re.sub(r"<[^>]+>", "", line)
            if stripped:
                cur_text.append(stripped)
    _flush()
    return segments


def _parse_vtt_ts(ts: str) -> float:
    """Parse ``HH:MM:SS.mmm`` or ``MM:SS.mmm`` into seconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(ts)
