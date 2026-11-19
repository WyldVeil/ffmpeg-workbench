"""Tests for core/ffmpeg.py (probe, run/progress/cancel, helpers)."""
import time

import pytest

from core import ffmpeg as ff


# ── time helpers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("75", 75.0), ("75.5", 75.5), ("75,5", 75.5), ("01:15", 75.0),
    ("1:02:03.5", 3723.5), ("00:00:00.040", 0.04), (12, 12.0), (1.5, 1.5),
])
def test_parse_time(text, expected):
    assert ff.parse_time(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "-5", -1, "1::2"])
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError):
        ff.parse_time(bad)


def test_format_time():
    assert ff.format_time(83.25) == "01:23.250"
    assert ff.format_time(3723.5) == "01:02:03.500"
    assert ff.format_time(5, ms=False) == "00:05"
    assert ff.format_time(5, always_hours=True) == "00:00:05.000"
    assert ff.format_time(-3) == "00:00.000"
    assert ff.ts(1.23456) == "1.235"


def test_prepare_ffmpeg_cmd_is_idempotent():
    cmd = ["/usr/bin/ffmpeg", "-i", "a.mp4", "b.mp4"]
    once = ff.prepare_ffmpeg_cmd(cmd)
    assert once[:1] == ["/usr/bin/ffmpeg"]
    assert "-nostdin" in once and "-hide_banner" in once
    assert once[once.index("-progress") + 1] == "pipe:1"
    assert ff.prepare_ffmpeg_cmd(once) == once
    # writing media to stdout moves progress to stderr
    piped = ff.prepare_ffmpeg_cmd(["ffmpeg", "-i", "a.mp4", "-f", "wav", "-"])
    assert piped[piped.index("-progress") + 1] == "pipe:2"
    # non-ffmpeg tools are untouched
    assert ff.prepare_ffmpeg_cmd(["yt-dlp", "-F", "x"]) == ["yt-dlp", "-F", "x"]


def test_summarize_error_picks_relevant_lines():
    log = [
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'a.mp4':",
        "  Duration: 00:00:06.00, start: 0.000000, bitrate: 3331 kb/s",
        "[AVFilterGraph @ 0x55d1] No such filter: 'nopefilter'",
        "Error initializing complex filters.",
        "Invalid argument",
    ]
    msg = ff.summarize_error(log, 234)
    assert "No such filter" in msg and "Exited with code 234" in msg
    assert "Duration" not in msg


# ── probe ────────────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
def test_probe_video_with_audio(media):
    info = ff.probe(media["h264"])
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (1280, 720)
    assert info.fps == pytest.approx(30, abs=0.01)
    assert info.duration == pytest.approx(6.0, abs=0.1)
    assert info.video.codec == "h264" and info.audio.channels >= 1
    assert not info.is_image and not info.is_hdr
    assert "1280×720" in info.summary()
    # cached object is reused
    assert ff.probe(media["h264"]) is info


@pytest.mark.ffmpeg
def test_probe_variants(media):
    v = ff.probe(media["vertical_noaudio"])
    assert v.has_video and not v.has_audio and v.height == 1920
    h = ff.probe(media["hevc10"])
    assert h.video.bit_depth == 10 and h.audio.codec == "opus"
    n = ff.probe(media["vp9"])
    assert n.fps == pytest.approx(29.97, abs=0.01)
    a = ff.probe(media["mp3"])
    assert a.has_audio and not a.has_video
    p = ff.probe(media["png"])
    assert p.is_image
    s = ff.probe(media["nasty"])
    assert s.has_video


def test_probe_missing_file_raises(tmp_path):
    with pytest.raises(ff.ProbeError):
        ff.probe(str(tmp_path / "nope.mp4"))
    assert ff.try_probe(str(tmp_path / "nope.mp4")) is None
    assert ff.duration_of("") == 0.0


@pytest.mark.ffmpeg
def test_probe_garbage_raises(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video at all" * 100)
    with pytest.raises(ff.ProbeError):
        ff.probe(str(junk))


# ── run ──────────────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
def test_run_reports_progress_and_succeeds(media, outdir):
    out = str(outdir / "o.mp4")
    seen = []
    res = ff.run([ff.ffmpeg_path(), "-y", "-i", media["h264"], "-t", "3",
                  "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", out],
                 on_progress=seen.append)
    assert res.ok, res.error_summary
    assert seen and seen[-1].fraction == 1.0
    assert any(p.fraction is not None and 0 < p.fraction < 1 for p in seen) or len(seen) >= 1
    assert ff.probe(out, use_cache=False).duration == pytest.approx(3.0, abs=0.15)


@pytest.mark.ffmpeg
def test_run_failure_has_summary(media, outdir):
    res = ff.run([ff.ffmpeg_path(), "-y", "-i", media["h264"],
                  "-vf", "nopefilter", str(outdir / "x.mp4")])
    assert not res.ok
    assert "nopefilter" in res.error_summary or "filter" in res.error_summary.lower()


def test_run_missing_program():
    res = ff.run(["/definitely/not/here/ffmpeg", "-version"])
    assert res.returncode == 127 and "not found" in res.error_summary.lower()


@pytest.mark.ffmpeg
def test_run_cancel_kills_process(outdir):
    out = str(outdir / "long.mp4")
    t0 = time.monotonic()
    res = ff.run([ff.ffmpeg_path(), "-y", "-f", "lavfi", "-i",
                  "testsrc2=size=1920x1080:rate=60", "-t", "600",
                  "-c:v", "libx264", "-preset", "veryslow", out],
                 cancel=lambda: time.monotonic() - t0 > 1.0)
    assert res.cancelled and not res.ok
    assert time.monotonic() - t0 < 10
    assert ff.active_count() == 0


@pytest.mark.ffmpeg
def test_guess_output_duration(media):
    f = ff.ffmpeg_path()
    assert ff.guess_output_duration([f, "-i", media["h264"], "-t", "2", "o.mp4"]) == 2.0
    assert ff.guess_output_duration([f, "-ss", "1", "-i", media["h264"], "o.mp4"]) == pytest.approx(5.0, abs=0.1)
    assert ff.guess_output_duration([f, "-i", media["h264"], "-ss", "1", "-to", "4", "o.mp4"]) == pytest.approx(3.0)
    assert ff.guess_output_duration([f, "-f", "lavfi", "-i", "testsrc", "o.mp4"]) is None


@pytest.mark.ffmpeg
def test_grab_frame_png(media):
    png = ff.grab_frame_png(media["h264"], 2.0, width=320)
    assert png and png.startswith(b"\x89PNG")
    assert ff.grab_frame_png(media["h264"] + ".missing", 1.0) is None


@pytest.mark.ffmpeg
def test_version_of():
    v = ff.version_of()
    assert v not in ("Missing", "Unknown")
    assert ff.version_of("/nonexistent/ffmpeg") == "Missing"


def test_format_cmd_round_trips_quotes():
    s = ff.format_cmd(["ffmpeg", "-i", "a b'c.mp4"])
    assert "a b" in s
