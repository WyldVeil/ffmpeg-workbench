"""Tests for core/encoders.py - every argument set must actually encode."""

import os

import pytest

from core import encoders as enc
from core import ffmpeg as ff


def test_quality_key_accepts_legacy_labels():
    assert enc.quality_key("Very High") == "very_high"
    assert enc.quality_key("good") == "good"
    assert enc.quality_key("nonsense") == "high"


def test_software_args_shape(monkeypatch):
    monkeypatch.setattr(enc, "preferred_hw", lambda: None)
    v = enc.video("h264", "high", hw="off", container="mp4")
    assert v.encoder == "libx264" and v.hw is None
    assert v.args[:2] == ["-c:v", "libx264"]
    assert "-crf" in v.args and "+faststart" in v.args
    assert v.args[v.args.index("-pix_fmt") + 1] == "yuv420p"
    h = enc.video("hevc", "good", hw="off", container="mp4")
    assert "hvc1" in h.args
    m = enc.video("hevc", "good", hw="off", container="mkv")
    assert "hvc1" not in m.args and "+faststart" not in m.args
    vp9 = enc.video("vp9", "high", hw="off", container="webm")
    assert vp9.args[vp9.args.index("-b:v") + 1] == "0"


def test_vf_merging():
    v = enc.VideoEncode(
        encoder="h264_vaapi", codec="h264", hw="vaapi", filter_suffix="format=nv12,hwupload"
    )
    assert v.vf("scale=1280:-2") == "scale=1280:-2,format=nv12,hwupload"
    assert v.vf(None) == "format=nv12,hwupload"
    plain = enc.VideoEncode(encoder="libx264", codec="h264")
    assert plain.vf(None) is None and plain.vf_args("hflip") == ["-vf", "hflip"]


def test_audio_args_and_containers():
    assert enc.audio("aac", "160k") == ["-c:a", "aac", "-b:a", "160k"]
    assert enc.audio("mp3", vbr_quality=2) == ["-c:a", "libmp3lame", "-q:a", "2"]
    # vorbis is not allowed in mp4 -> falls back to the container default
    assert enc.audio("vorbis", container="mp4")[:2] == ["-c:a", "aac"]
    assert enc.audio("aac", container="webm")[:2] == ["-c:a", "libopus"]
    assert enc.audio("copy") == ["-c:a", "copy"]
    assert enc.default_audio_for("webm") == "opus"
    assert enc.audio_key_from_probe("pcm_s16le") == "pcm16"
    assert enc.video_ok_for("webm", "vp9") and not enc.video_ok_for("webm", "h264")


def test_two_pass_args():
    assert enc.two_pass_args("libx264", 1, "/tmp/p") == ["-pass", "1", "-passlogfile", "/tmp/p"]
    assert "pass=2" in enc.two_pass_args("libx265", 2, "/tmp/p")[1]


def _encode_ok(v, outdir, name, container):
    out = os.path.join(outdir, f"{name}.{container}")
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        *v.pre_input,
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=30",
        "-t",
        "1",
        *v.vf_args("scale=640:-2"),
        *v.args,
        "-an",
        out,
    ]
    res = ff.run(cmd)
    assert res.ok, f"{v.encoder} {v.args}: {res.error_summary}"
    info = ff.probe(out, use_cache=False)
    assert info.has_video and info.duration > 0.5
    return info


SOFTWARE_MATRIX = [
    ("h264", "mp4"),
    ("hevc", "mp4"),
    ("av1", "mkv"),
    ("vp9", "webm"),
    ("prores", "mov"),
    ("dnxhr", "mov"),
    ("ffv1", "mkv"),
    ("mpeg4", "avi"),
]


@pytest.mark.ffmpeg
@pytest.mark.parametrize("codec,container", SOFTWARE_MATRIX)
@pytest.mark.parametrize("quality", ["very_high", "lowest"])
def test_software_encoders_really_encode(codec, container, quality, outdir, monkeypatch):
    v = enc.video(codec, quality, speed="fastest", hw="off", container=container)
    if v.encoder not in enc.list_encoders():
        pytest.skip(f"{v.encoder} not in this ffmpeg")
    info = _encode_ok(v, outdir, f"{codec}_{quality}", container)
    if codec == "hevc" and container == "mp4":
        assert info.require_video().raw.get("codec_tag_string") == "hvc1"


@pytest.mark.ffmpeg
@pytest.mark.parametrize("codec", ["h264", "hevc", "av1"])
def test_ten_bit_software(codec, outdir):
    v = enc.video(codec, "good", speed="fastest", hw="off", ten_bit=True, container="mkv")
    if v.encoder not in enc.list_encoders():
        pytest.skip("encoder missing")
    info = _encode_ok(v, outdir, f"{codec}_10", "mkv")
    assert info.require_video().bit_depth == 10


@pytest.mark.ffmpeg
def test_hw_probe_is_cached_and_hw_args_encode(outdir):
    hw = enc.hw_available()
    assert isinstance(hw, dict)
    assert enc.hw_available() == hw  # cached
    working = [k for k, ok in hw.items() if ok]
    if not working:
        pytest.skip("no working hardware encoder on this machine")
    for kind in working:
        for codec in ("h264", "hevc", "av1"):
            if (codec, kind) not in enc._HW or enc._HW[(codec, kind)] not in enc.list_encoders():
                continue
            for speed in ("fastest", "slowest"):
                v = enc.video(codec, "high", speed=speed, hw=kind, container="mp4")
                if v.hw != kind:
                    continue
                try:
                    _encode_ok(v, outdir, f"{kind}_{codec}_{speed}", "mp4")
                except AssertionError as exc:
                    # some GPUs lack AV1 / HEVC encode - only the probe codec must work
                    if codec == "h264":
                        raise
                    print(f"note: {kind}/{codec} unavailable: {exc}")


@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "codec,container",
    [
        ("aac", "m4a"),
        ("mp3", "mp3"),
        ("opus", "opus"),
        ("vorbis", "ogg"),
        ("flac", "flac"),
        ("alac", "m4a"),
        ("pcm16", "wav"),
        ("ac3", "ac3"),
    ],
)
def test_audio_encoders_really_encode(codec, container, media, outdir):
    out = os.path.join(outdir, f"a.{container}")
    res = ff.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            media["wav_silence"],
            *enc.audio(codec, "128k", container=container),
            out,
        ]
    )
    assert res.ok, res.error_summary
    assert ff.probe(out, use_cache=False).has_audio
