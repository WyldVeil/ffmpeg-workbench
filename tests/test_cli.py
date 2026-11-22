"""The command line (core/cli.py): every operation on real files, plus the
safety rules (inputs never touched, no silent overwrites, JSON results)."""
import json
import os
import re
import subprocess
import sys

import pytest

from core import cli
from core import ffmpeg as ff

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(capsys, *argv):
    code = cli.main([*argv, "--json"] if argv and argv[0] not in ("tools",) else list(argv))
    out = capsys.readouterr().out.strip().splitlines()
    data = json.loads(out[-1]) if out else {}
    return code, data


def ok(capsys, *argv):
    code, data = run(capsys, *argv)
    assert code == 0 and data.get("ok"), data
    if data.get("output") and not os.path.isdir(data["output"]):
        assert os.path.getsize(data["output"]) > 0
        assert not os.path.exists(cli.part_of(data["output"]))
    return data


def info(path):
    return ff.probe(path, use_cache=False)


# ── catalogue / info ─────────────────────────────────────────────────────────

def test_tools_catalogue_json(capsys):
    assert cli.main(["tools", "--json"]) == 0
    cat = json.loads(capsys.readouterr().out)
    names = {op["name"] for op in cat["operations"]}
    assert {"trim", "compress", "collage", "metadata-clean", "download"} <= names
    trim = next(op for op in cat["operations"] if op["name"] == "trim")
    assert any(o["name"] == "--start" for o in trim["options"])
    assert all(op["summary"] and op["category"] for op in cat["operations"])


@pytest.mark.ffmpeg
def test_info(capsys, media):
    d = ok(capsys, "info", media["h264"])
    assert d["duration"] == pytest.approx(6.0, abs=0.1)
    assert [s["type"] for s in d["streams"]] == ["video", "audio"]
    assert d["streams"][0]["width"] == 1280


def test_agents_file_lists_every_operation():
    text = open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()
    missing = [name for name in cli.OPERATIONS if not re.search(rf"`{re.escape(name)}`", text)]
    assert not missing, f"AGENTS.md doesn't mention: {missing}"


# ── safety rules ─────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
def test_never_overwrites_without_permission(capsys, media, tmp_path):
    out = tmp_path / "taken.mp4"
    out.write_bytes(b"keep me")
    code, data = run(capsys, "trim", media["h264"], "--end", "1", "-o", str(out))
    assert code == 1 and not data["ok"] and "already exists" in data["error"]
    assert out.read_bytes() == b"keep me"
    ok(capsys, "trim", media["h264"], "--end", "1", "-o", str(out), "--overwrite")
    assert info(str(out)).duration == pytest.approx(1.0, abs=0.1)


@pytest.mark.ffmpeg
def test_refuses_to_replace_the_input(capsys, media, tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(open(media["h264"], "rb").read())
    before = src.read_bytes()
    code, data = run(capsys, "speed", str(src), "--factor", "2", "-o", str(src), "--overwrite")
    assert code == 1 and "replace the input" in data["error"]
    assert src.read_bytes() == before


@pytest.mark.ffmpeg
def test_default_output_next_to_input_never_clobbers(capsys, media, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(open(media["h264_b"], "rb").read())
    a = ok(capsys, "trim", str(src), "--end", "1")["output"]
    b = ok(capsys, "trim", str(src), "--end", "1")["output"]
    assert a != b and os.path.dirname(a) == str(tmp_path) and os.path.isfile(a) and os.path.isfile(b)


def test_errors_are_json(capsys, tmp_path):
    code, data = run(capsys, "trim", str(tmp_path / "missing.mp4"))
    assert code == 1 and data == {"ok": False, "error": data["error"]} and "not found" in data["error"]


def test_download_respects_offline_mode(capsys, monkeypatch):
    from core import ytdlp
    monkeypatch.setenv("CROSSFADER_NO_NETWORK", "1")
    if not ytdlp.ytdlp_path():
        pytest.skip("yt-dlp not bundled here")
    code, data = run(capsys, "download", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert code == 1 and not data["ok"]


@pytest.mark.ffmpeg
def test_entry_point_runs_without_a_window(media):
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), "--cli", "info", media["vp9"], "--json"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["streams"][0]["codec"] == "vp9"
    assert "tkinter" not in r.stderr


# ── Cut ──────────────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
def test_trim_exact_and_copy(capsys, media, outdir):
    d = ok(capsys, "trim", media["h264"], "--start", "1.5", "--end", "00:00:04", "-o", str(outdir / "t.mp4"))
    assert d["duration"] == pytest.approx(2.5, abs=0.08)
    d = ok(capsys, "trim", media["h264"], "--start", "1", "--end", "4", "--copy", "-o", str(outdir / "c.mkv"))
    assert 2.5 <= d["duration"] <= 4.1


@pytest.mark.ffmpeg
def test_join_lossless_and_mixed(capsys, media, outdir):
    d = ok(capsys, "join", media["h264"], media["h264_b"], "-o", str(outdir / "j.mp4"))
    assert d["method"] == "lossless" and d["duration"] == pytest.approx(10.0, abs=0.15)
    d = ok(capsys, "join", media["h264"], media["vp9"], "-o", str(outdir / "m.mp4"))
    assert d["method"].startswith("re-encoded") and d["duration"] == pytest.approx(11.0, abs=0.2)
    code, data = run(capsys, "join", media["h264"], media["vp9"], "--copy", "-o", str(outdir / "x.mp4"))
    assert code == 1 and "losslessly" in data["error"]


@pytest.mark.ffmpeg
def test_transition(capsys, media, outdir):
    d = ok(capsys, "transition", media["h264"], media["h264_b"], "--type", "wipeleft", "--duration", "1",
           "-o", str(outdir / "x.mp4"))
    assert d["duration"] == pytest.approx(9.0, abs=0.15)


@pytest.mark.ffmpeg
def test_rotate(capsys, media, outdir):
    d = ok(capsys, "rotate", media["h264_b"], "--degrees", "90", "-o", str(outdir / "r.mp4"))
    v = info(d["output"]).video
    assert (v.width, v.height) == (720, 1280)
    d = ok(capsys, "rotate", media["h264_b"], "--degrees", "270", "--lossless", "-o", str(outdir / "l.mp4"))
    v = info(d["output"]).video
    assert (v.width, v.height, v.rotation) == (1280, 720, 270) and v.display_size == (720, 1280)


@pytest.mark.ffmpeg
def test_crop(capsys, media, outdir):
    d = ok(capsys, "crop", media["h264_b"], "--aspect", "1:1", "-o", str(outdir / "sq.mp4"))
    v = info(d["output"]).video
    assert (v.width, v.height) == (720, 720)
    d = ok(capsys, "crop", media["h264_b"], "--rect", "100:50:640:360", "-o", str(outdir / "r.mp4"))
    assert (info(d["output"]).video.width, info(d["output"]).video.height) == (640, 360)


@pytest.mark.ffmpeg
def test_speed_and_reverse(capsys, media, outdir):
    d = ok(capsys, "speed", media["h264"], "--factor", "2", "-o", str(outdir / "s.mp4"))
    assert d["duration"] == pytest.approx(3.0, abs=0.1) and "audio" in d
    d = ok(capsys, "speed", media["mp3"], "--factor", "0.5", "-o", str(outdir / "s.mp3"))
    assert d["duration"] == pytest.approx(16.0, abs=0.2)
    d = ok(capsys, "reverse", media["h264_b"], "-o", str(outdir / "r.mp4"))
    assert d["duration"] == pytest.approx(4.0, abs=0.1)


# ── Audio ────────────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
def test_remove_silence(capsys, media, outdir):
    d = ok(capsys, "remove-silence", media["wav_silence"], "-o", str(outdir / "n.wav"))
    assert d["removed_seconds"] > 1.0 and d["duration"] < 5.5


@pytest.mark.ffmpeg
def test_audio_ops(capsys, media, outdir):
    d = ok(capsys, "extract-audio", media["h264"], "--format", "flac", "-o", str(outdir / "a.flac"))
    assert d["audio"].startswith("flac")
    d = ok(capsys, "extract-audio", media["h264"], "--copy", "-o", str(outdir))
    assert d["output"].endswith(".m4a")
    d = ok(capsys, "normalize", media["h264"], "--target", "youtube", "-o", str(outdir / "n.mp4"))
    assert "video" in d and "audio" in d
    d = ok(capsys, "clean-voice", media["hevc10"], "-o", str(outdir / "c.mkv"))
    assert "video" in d and "audio" in d


@pytest.mark.skipif(not __import__("core.tts", fromlist=["x"]).system_engine(), reason="no offline voice")
def test_tts_offline(capsys, outdir):
    d = ok(capsys, "tts", "--text", "Testing one two three.", "--offline", "-o", str(outdir / "t.mp3"))
    assert d["engine"] == "system" and d["duration"] > 0.5


# ── Convert ──────────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
def test_compress_hits_the_size(capsys, media, outdir):
    d = ok(capsys, "compress", media["h264_1080p25"], "--size", "400KB", "-o", str(outdir / "small.mp4"))
    assert d["size_bytes"] <= 400_000


@pytest.mark.ffmpeg
def test_convert(capsys, media, outdir):
    d = ok(capsys, "convert", media["vp9"], "--to", "mkv", "-o", str(outdir / "c.mkv"))
    assert d["video"].startswith("vp9")                      # copied, not re-encoded
    d = ok(capsys, "convert", media["vp9"], "--to", "mp3", "-o", str(outdir / "c.mp3"))
    assert "video" not in d and d["audio"].startswith("mp3")


@pytest.mark.ffmpeg
def test_scale_and_framerate(capsys, media, outdir):
    d = ok(capsys, "scale", media["h264_b"], "--height", "360", "-o", str(outdir / "s.mp4"))
    assert "640x360" in d["video"]
    d = ok(capsys, "scale", media["vp9"], "--size", "1080x1080", "--fit", "pad", "-o", str(outdir / "p.mp4"))
    assert "1080x1080" in d["video"]
    d = ok(capsys, "framerate", media["vp9"], "--rate", "60", "--method", "blend", "-o", str(outdir / "f.mp4"))
    assert "60fps" in d["video"] and d["duration"] == pytest.approx(5.0, abs=0.15)
    d = ok(capsys, "framerate", media["h264_b"], "--rate", "30", "--slowmo", "2", "--method", "dup",
           "-o", str(outdir / "slow.mp4"))
    assert d["duration"] == pytest.approx(8.0, abs=0.2)


@pytest.mark.ffmpeg
def test_hdr_to_sdr(capsys, media, outdir):
    code, data = run(capsys, "hdr-to-sdr", media["hevc10"], "-o", str(outdir / "sdr.mp4"))
    assert code == 1 and "--assume" in data["error"]              # untagged: refuses instead of guessing
    d = ok(capsys, "hdr-to-sdr", media["hevc10"], "--assume", "pq", "-o", str(outdir / "sdr.mp4"))
    v = info(d["output"]).video
    assert v.pix_fmt == "yuv420p" and v.color_transfer in ("bt709", "")


# ── Social ───────────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
def test_subtitle_burn(capsys, media, outdir, tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nHello — ünïcödé\n", encoding="utf-8")
    ok(capsys, "burn-subs", media["h264_b"], "--subs", str(srt), "-o", str(outdir / "b.mp4"))


@pytest.mark.ffmpeg
def test_gif_and_images(capsys, media, outdir):
    d = ok(capsys, "gif", media["h264"], "--start", "1", "--duration", "2", "--width", "320",
           "-o", str(outdir / "a.gif"))
    assert d["output"].endswith(".gif")
    d = ok(capsys, "gif", media["h264"], "--duration", "3", "--max-mb", "0.3", "-o", str(outdir / "small.gif"))
    assert d["size_bytes"] <= 0.3 * 1024 * 1024
    d = ok(capsys, "thumbnail", media["h264"], "--at", "2", "--format", "png", "-o", str(outdir / "f.png"))
    assert info(d["output"]).video.width == 1280
    d = ok(capsys, "frames", media["h264"], "--count", "3", "-o", str(outdir / "frames"))
    assert d["files"] == 3


@pytest.mark.ffmpeg
def test_shorts_logo_collage_blur(capsys, media, outdir):
    d = ok(capsys, "shorts", media["h264"], "--platform", "shorts", "-o", str(outdir / "s.mp4"))
    assert "1080x1920" in d["video"] and "audio" in d
    d = ok(capsys, "logo", media["h264_b"], "--logo", media["png"], "--position", "tl", "-o", str(outdir / "l.mp4"))
    assert d["duration"] == pytest.approx(4.0, abs=0.1)
    d = ok(capsys, "collage", media["h264"], media["vertical_noaudio"], media["vp9"], "--size", "1280x720",
           "--audio", "mix", "-o", str(outdir / "c.mp4"))
    assert "1280x720" in d["video"] and d["duration"] == pytest.approx(6.0, abs=0.15) and d["layout"]
    d = ok(capsys, "blur-region", media["h264_b"], "--region", "10,10,30,30", "--region", "60,60,20,20",
           "--type", "pixelate", "-o", str(outdir / "b.mp4"))
    assert "audio" in d


# ── Visuals ──────────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
@pytest.mark.parametrize("argv", [["denoise", "--method", "fast"], ["sharpen", "--amount", "0.6"],
                                  ["deinterlace", "--mode", "all"]])
def test_filters(capsys, media, outdir, argv):
    d = ok(capsys, argv[0], media["vp9"], *argv[1:], "-o", str(outdir / f"{argv[0]}.mp4"))
    assert d["duration"] == pytest.approx(5.0, abs=0.15)


@pytest.mark.ffmpeg
def test_lut(capsys, media, outdir):
    from tabs.visuals import lut
    cube = lut.builtin_lut_path(next(iter(lut.BUILTIN)))
    ok(capsys, "lut", media["vp9"], "--lut", cube, "--intensity", "0.7", "-o", str(outdir / "g.mp4"))


# ── System ───────────────────────────────────────────────────────────────────

@pytest.mark.ffmpeg
def test_metadata_clean(capsys, media, outdir, tmp_path):
    tagged = str(tmp_path / "tagged.mp4")
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-i", media["h264_b"], "-c", "copy",
                    "-metadata", "location=+51.5074-000.1278/", "-metadata", "title=My holiday", tagged], check=True)
    d = ok(capsys, "metadata-clean", tagged, "-o", str(outdir / "clean.mp4"))
    assert any("location" in r.lower() for r in d["removed"]) and d["still_there"] == []


WHISPER_ASSETS = os.environ.get("CROSSFADER_TEST_WHISPER_DIR", "")


@pytest.mark.ffmpeg
@pytest.mark.skipif(not (WHISPER_ASSETS and os.path.isfile(os.path.join(WHISPER_ASSETS, "ggml-base-q5_1.bin"))
                         and os.path.isfile(os.path.join(WHISPER_ASSETS, "jfk.wav"))),
                    reason="set CROSSFADER_TEST_WHISPER_DIR (ggml-base-q5_1.bin + jfk.wav)")
def test_subtitles_from_speech(capsys, outdir):
    import shutil

    from core import whisper
    shutil.copyfile(os.path.join(WHISPER_ASSETS, "ggml-base-q5_1.bin"), whisper.model_path("base"))
    with open(whisper.model_path("base") + ".ok", "w") as fh:
        fh.write("test\n")
    video = str(outdir / "speech.mp4")
    ff.check([ff.ffmpeg_path(), "-y", "-f", "lavfi", "-i", "color=c=0x334455:s=640x360:d=11", "-i",
              os.path.join(WHISPER_ASSETS, "jfk.wav"), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
              "-c:a", "aac", video])
    d = ok(capsys, "subtitles", video, "--model", "base", "--language", "en")
    assert d["output"] == str(outdir / "speech.srt")
    assert "country" in open(d["output"], encoding="utf-8").read().lower()
    d = ok(capsys, "subtitles", video, "--model", "base", "--language", "en", "--burn", "-o", str(outdir / "b.mp4"))
    assert d["duration"] == pytest.approx(11.0, abs=0.2)
