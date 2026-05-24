"""Tests for the four-path transcript fetcher.

All paths are mocked at the seams the spec names:
  - ``YouTubeTranscriptApi`` (module-level binding)
  - ``subprocess.run`` (for yt-dlp invocations)
  - ``_download`` (urllib wrapper for podcast MP3)
  - The whisper model (a fake passed directly)
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.narrative.sources.youtube import transcript_fetchers as tf
from src.narrative.sources.youtube.chunker import Segment
from src.narrative.sources.youtube.discovery import VideoCandidate


# --------------------------------------------------------------------- #
# Fixtures + helpers                                                    #
# --------------------------------------------------------------------- #


def _yt_candidate(video_id: str = "vid00000001") -> VideoCandidate:
    return VideoCandidate(
        channel="Lex Fridman Podcast",
        channel_id="UCSHZKyawb77ixDdsGog4iWA",
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="Jensen on AI",
        published_utc=datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc),
        reason="speaker:jensen_huang",
        matched_speaker="jensen_huang",
        matched_tickers=("NVDA",),
        matched_text="[TITLE] Jensen on AI",
        match_source="title",
        source="youtube",
    )


def _pc_candidate(video_id: str = "pc_abc12345678") -> VideoCandidate:
    return VideoCandidate(
        channel="Lex Fridman Podcast",
        channel_id="podcast:lex_fridman_podcast",
        video_id=video_id,
        url="https://lexfridman.com/jensen",
        title="Jensen on AI",
        published_utc=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
        reason="speaker:jensen_huang",
        matched_speaker="jensen_huang",
        matched_tickers=("NVDA",),
        matched_text="[TITLE] Jensen on AI",
        match_source="title",
        source="podcast",
        audio_url="https://media.lexfridman.com/ep.mp3",
        episode_guid="lex-ep-1",
    )


def _fake_whisper(text: str = "transcribed hello", duration: float = 180.0) -> MagicMock:
    model = MagicMock()
    seg = SimpleNamespace(start=0.0, end=5.0, text=text)
    info = SimpleNamespace(duration=duration)
    model.transcribe.return_value = (iter([seg]), info)
    return model


# --------------------------------------------------------------------- #
# Path 0 — podcast MP3 + Whisper                                        #
# --------------------------------------------------------------------- #


def test_path0_no_audio_url_raises(tmp_path: Path) -> None:
    cand_no_url = dataclasses.replace(_pc_candidate(), audio_url=None)
    with pytest.raises(tf.TranscriptUnavailable, match="no audio_url"):
        tf.fetch_via_podcast_audio(
            cand_no_url, workdir=tmp_path, whisper_model=_fake_whisper(),
        )


def test_path0_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cand = _pc_candidate()
    monkeypatch.setattr(
        tf, "_download",
        lambda url, dest, **kw: Path(dest).write_bytes(b"fake mp3 bytes"),
    )
    result = tf.fetch_via_podcast_audio(
        cand, workdir=tmp_path, whisper_model=_fake_whisper("hello world"),
    )
    assert result.source_method == "podcast_rss"
    assert result.duration_s == 180.0
    assert len(result.segments) == 1
    assert result.segments[0].text == "hello world"
    # Cleanup: the .mp3 must not survive after success
    assert not (tmp_path / f"{cand.video_id}.mp3").exists()


# --------------------------------------------------------------------- #
# Path 1 — youtube-transcript-api                                       #
# --------------------------------------------------------------------- #


def test_path1_success(monkeypatch: pytest.MonkeyPatch) -> None:
    snippet = SimpleNamespace(text="Hello from caption", start=0.0, duration=5.0)
    fake_class = MagicMock()
    fake_class.return_value.fetch.return_value = [snippet]
    monkeypatch.setattr(tf, "YouTubeTranscriptApi", fake_class)

    result = tf.fetch_via_transcript_api("vid00000001")
    assert result.source_method == "transcript_api"
    assert len(result.segments) == 1
    assert result.segments[0].text == "Hello from caption"
    assert result.segments[0].end_s == 5.0


def test_path1_wraps_arbitrary_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_class = MagicMock()
    fake_class.return_value.fetch.side_effect = RuntimeError("RequestBlocked: IP")
    monkeypatch.setattr(tf, "YouTubeTranscriptApi", fake_class)

    with pytest.raises(tf.TranscriptUnavailable, match="RuntimeError"):
        tf.fetch_via_transcript_api("vid00000001")


def test_path1_empty_result_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_class = MagicMock()
    fake_class.return_value.fetch.return_value = []
    monkeypatch.setattr(tf, "YouTubeTranscriptApi", fake_class)
    with pytest.raises(tf.TranscriptUnavailable, match="0 segments"):
        tf.fetch_via_transcript_api("vid00000001")


def test_path1_no_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tf, "YouTubeTranscriptApi", None)
    with pytest.raises(tf.TranscriptUnavailable, match="not installed"):
        tf.fetch_via_transcript_api("vid00000001")


# --------------------------------------------------------------------- #
# Proxy plumbing                                                        #
# --------------------------------------------------------------------- #


def test_has_proxy_detects_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    assert tf.has_proxy() is False

    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "user")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "")  # half-set
    assert tf.has_proxy() is False

    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "pwd")
    assert tf.has_proxy() is True


def test_yt_dlp_proxy_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    assert tf._yt_dlp_proxy_args() == []

    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "u")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "p")
    args = tf._yt_dlp_proxy_args()
    assert args == ["--proxy", "http://u:p@p.webshare.io:80"]


def test_build_transcript_api_uses_proxy_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict = {}

    class FakeApi:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(tf, "YouTubeTranscriptApi", FakeApi)
    # Pretend WebshareProxyConfig exists as a module attribute we can patch
    import sys
    fake_proxies = SimpleNamespace(WebshareProxyConfig=lambda **kw: ("Webshare", kw))
    monkeypatch.setitem(sys.modules, "youtube_transcript_api.proxies", fake_proxies)

    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "u")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "p")
    tf._build_transcript_api()
    assert "proxy_config" in captured_kwargs
    assert captured_kwargs["proxy_config"] == ("Webshare", {"proxy_username": "u", "proxy_password": "p"})


def test_build_transcript_api_no_proxy_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict = {}

    class FakeApi:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(tf, "YouTubeTranscriptApi", FakeApi)
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    tf._build_transcript_api()
    assert captured_kwargs == {}


def test_dispatch_youtube_no_proxy_raises_clear_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without proxy creds, YouTube candidates must fail with
    ``no_proxy_configured`` rather than three sequential network errors."""
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    with pytest.raises(tf.TranscriptUnavailable, match="no_proxy_configured"):
        tf.fetch_transcript(
            _yt_candidate(), workdir=tmp_path, whisper_model=_fake_whisper(),
        )


# --------------------------------------------------------------------- #
# Path 2 — yt-dlp captions                                              #
# --------------------------------------------------------------------- #


_SAMPLE_VTT = """WEBVTT

00:00:00.000 --> 00:00:05.000
Hello world from auto captions.

00:00:05.000 --> 00:00:10.000
Second cue with NVDA mention.
"""


def test_path2_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video_id = "vid12345abc"
    vtt_path = tmp_path / f"{video_id}.en.vtt"

    def fake_run(cmd, **kw):
        # Simulate yt-dlp writing the VTT we expect
        vtt_path.write_text(_SAMPLE_VTT)
        return MagicMock(returncode=0)

    monkeypatch.setattr(tf.subprocess, "run", fake_run)

    result = tf.fetch_via_yt_dlp_subs(video_id, workdir=tmp_path)
    assert result.source_method == "yt_dlp_subs"
    assert len(result.segments) == 2
    assert "Hello world" in result.segments[0].text
    assert "NVDA" in result.segments[1].text
    # cleanup happened
    assert not vtt_path.exists()


def test_path2_no_vtt_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        tf.subprocess, "run",
        MagicMock(return_value=MagicMock(returncode=0)),
    )
    with pytest.raises(tf.TranscriptUnavailable, match="no VTT"):
        tf.fetch_via_yt_dlp_subs("absent01video", workdir=tmp_path)


def test_path2_yt_dlp_missing_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        tf.subprocess, "run",
        MagicMock(side_effect=FileNotFoundError("yt-dlp not installed")),
    )
    with pytest.raises(tf.TranscriptUnavailable, match="yt-dlp subs failed"):
        tf.fetch_via_yt_dlp_subs("vid00000001", workdir=tmp_path)


# --------------------------------------------------------------------- #
# Path 3 — yt-dlp audio + Whisper                                       #
# --------------------------------------------------------------------- #


def test_path3_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video_id = "vid_whispertest"
    wav_path = tmp_path / f"{video_id}.wav"

    def fake_run(cmd, **kw):
        wav_path.write_bytes(b"fake wav")
        return MagicMock(returncode=0)

    monkeypatch.setattr(tf.subprocess, "run", fake_run)
    result = tf.fetch_via_whisper(
        video_id, workdir=tmp_path, whisper_model=_fake_whisper("Whispered text", duration=300.0),
    )
    assert result.source_method == "whisper"
    assert result.duration_s == 300.0
    assert result.segments[0].text == "Whispered text"
    assert not wav_path.exists()


def test_path3_no_wav_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        tf.subprocess, "run",
        MagicMock(return_value=MagicMock(returncode=0)),
    )
    with pytest.raises(tf.TranscriptUnavailable, match="no WAV"):
        tf.fetch_via_whisper(
            "vid00000001", workdir=tmp_path, whisper_model=_fake_whisper(),
        )


# --------------------------------------------------------------------- #
# Dispatcher                                                            #
# --------------------------------------------------------------------- #


def test_dispatch_podcast_goes_to_path0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Podcast source must only call Path 0, never YT paths."""
    called: list[str] = []
    monkeypatch.setattr(tf, "fetch_via_podcast_audio",
                        lambda *a, **kw: called.append("p0") or
                        tf.TranscriptResult((), "podcast_rss", 1.0))
    monkeypatch.setattr(tf, "fetch_via_transcript_api",
                        lambda *a, **kw: called.append("p1"))
    monkeypatch.setattr(tf, "fetch_via_yt_dlp_subs",
                        lambda *a, **kw: called.append("p2"))
    monkeypatch.setattr(tf, "fetch_via_whisper",
                        lambda *a, **kw: called.append("p3"))

    tf.fetch_transcript(
        _pc_candidate(), workdir=tmp_path, whisper_model=_fake_whisper(),
    )
    assert called == ["p0"]


def test_dispatch_podcast_without_whisper_raises(tmp_path: Path) -> None:
    with pytest.raises(tf.TranscriptUnavailable, match="requires"):
        tf.fetch_transcript(
            _pc_candidate(), workdir=tmp_path, whisper_model=None,
        )


def test_dispatch_youtube_path1_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When Path 1 succeeds, Paths 2 and 3 must not be called."""
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "u")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "p")
    called: list[str] = []
    monkeypatch.setattr(
        tf, "fetch_via_transcript_api",
        lambda *a, **kw: (called.append("p1"),
                          tf.TranscriptResult(
                              (Segment(0.0, 5.0, "ok"),), "transcript_api", 5.0,
                          ))[1],
    )
    monkeypatch.setattr(tf, "fetch_via_yt_dlp_subs",
                        lambda *a, **kw: called.append("p2"))
    monkeypatch.setattr(tf, "fetch_via_whisper",
                        lambda *a, **kw: called.append("p3"))

    result = tf.fetch_transcript(
        _yt_candidate(), workdir=tmp_path, whisper_model=_fake_whisper(),
    )
    assert called == ["p1"]
    assert result.source_method == "transcript_api"


def test_dispatch_youtube_falls_through_to_whisper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both caption paths raise → Path 3 (whisper) is invoked."""
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "u")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "p")
    called: list[str] = []

    def raise_unavailable(name: str):
        def _fn(*a, **kw):
            called.append(name)
            raise tf.TranscriptUnavailable(f"{name} disabled")
        return _fn

    monkeypatch.setattr(tf, "fetch_via_transcript_api", raise_unavailable("p1"))
    monkeypatch.setattr(tf, "fetch_via_yt_dlp_subs", raise_unavailable("p2"))
    monkeypatch.setattr(
        tf, "fetch_via_whisper",
        lambda *a, **kw: (called.append("p3"),
                          tf.TranscriptResult(
                              (Segment(0.0, 5.0, "whisper"),), "whisper", 5.0,
                          ))[1],
    )

    result = tf.fetch_transcript(
        _yt_candidate(), workdir=tmp_path, whisper_model=_fake_whisper(),
    )
    assert called == ["p1", "p2", "p3"]
    assert result.source_method == "whisper"


def test_dispatch_no_whisper_all_paths_fail_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If caption paths both fail AND no whisper, surface
    TranscriptUnavailable."""
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "u")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "p")
    def raise_(*a, **kw):
        raise tf.TranscriptUnavailable("nope")

    monkeypatch.setattr(tf, "fetch_via_transcript_api", raise_)
    monkeypatch.setattr(tf, "fetch_via_yt_dlp_subs", raise_)
    with pytest.raises(tf.TranscriptUnavailable, match="captions unavailable"):
        tf.fetch_transcript(
            _yt_candidate(), workdir=tmp_path, whisper_model=None,
        )


def test_dispatch_disable_youtube_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Set proxy so the disable-flag path is what's exercised, not no_proxy.
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "u")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "p")
    with pytest.raises(tf.TranscriptUnavailable, match="disabled"):
        tf.fetch_transcript(
            _yt_candidate(), workdir=tmp_path, whisper_model=_fake_whisper(),
            allow_youtube_paths=False,
        )


# --------------------------------------------------------------------- #
# VTT parser sanity                                                     #
# --------------------------------------------------------------------- #


def test_parse_vtt_extracts_cues() -> None:
    segments = tf.parse_vtt(_SAMPLE_VTT)
    assert len(segments) == 2
    assert segments[0].start_s == 0.0
    assert segments[0].end_s == 5.0
    assert "Hello world" in segments[0].text
    assert segments[1].start_s == 5.0


def test_parse_vtt_strips_inline_tags() -> None:
    vtt = ("WEBVTT\n\n00:00.000 --> 00:05.000\n"
           "<c.colorE5E5E5>Hello</c> <c>world</c>\n")
    segments = tf.parse_vtt(vtt)
    assert segments[0].text == "Hello world"


def test_parse_vtt_handles_short_timestamp() -> None:
    vtt = "WEBVTT\n\n00:05.000 --> 00:12.500\nShort timestamp form.\n"
    segments = tf.parse_vtt(vtt)
    assert segments[0].start_s == 5.0
    assert segments[0].end_s == 12.5
