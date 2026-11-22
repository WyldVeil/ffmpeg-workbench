"""Transcoder tools: Downsizer, Format Converter, Preset Manager (+ the rest as they are rebuilt)."""

import os
import pathlib
import shutil

import pytest

from core import ffmpeg as ff


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


def _ok(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


# ── Preset Manager ──────────────────────────────────────────────────────────


def test_preset_templates_are_not_shell():
    from tabs.transcoder import presets as pr

    for bad in (
        "-i {INPUT} -c copy {OUTPUT} && rm -rf ~",
        "-i {INPUT} {OUTPUT} | tee x",
        "-i {INPUT} {OUTPUT} > log",
        "-i {INPUT} -c copy x.mp4",
        "-c copy {OUTPUT}",
    ):
        with pytest.raises(ValueError):
            pr.check_template(bad)
    cmd = pr.build_command(
        'ffmpeg -i {INPUT} -vf "scale=640:-2,setsar=1" {OUTPUT}', r"C:\clips\a b.mp4", "/o/x.mp4"
    )
    assert cmd[-5:] == ["-i", r"C:\clips\a b.mp4", "-vf", "scale=640:-2,setsar=1", "/o/x.mp4"]
    for p in pr.BUILT_IN:
        pr.check_template(p["cmd"])


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_preset_run_with_hostile_file_name(ui, media, tmp_path, outdir):
    src = tmp_path / 'clip"; touch wme_pwned_marker; echo $(touch wme_pwned_marker2) ".mp4'
    shutil.copyfile(media["h264"], src)
    markers = [
        d / m
        for d in (tmp_path, pathlib.Path.cwd(), outdir)
        for m in ("wme_pwned_marker", "wme_pwned_marker2")
    ]
    ui.reset()
    tab = ui.open("Preset Manager")
    from tabs.transcoder import presets as pr

    tab._selected_idx = next(
        i for i, p in enumerate(tab.presets) if p["cmd"] == pr.BUILT_IN[5]["cmd"]
    )  # audio only
    tab.run_src_var.set(str(src))
    out = str(outdir / "preset.m4a")
    tab.run_out_var.set(out)
    tab._quick_run()
    ui.wait_idle(120)
    _ok(ui)
    assert not any(m.exists() for m in markers)
    info = ff.probe(out, use_cache=False)
    assert info.has_audio and not info.has_video


def test_presets_file_corrupt_is_kept(tmp_path, monkeypatch):
    from tabs.transcoder import presets as pr

    path = tmp_path / "encode_presets.json"
    path.write_text("{not json")
    monkeypatch.setattr(pr, "presets_file", lambda: str(path))
    presets, warning = pr._load_presets()
    assert warning and len(presets) == len(pr.BUILT_IN)
    assert not path.exists() and any(
        f.name.startswith("encode_presets.json.unreadable-") for f in tmp_path.iterdir()
    )
    assert pr._save_user_presets([*presets, {"name": "Mine", "cmd": "-i {INPUT} {OUTPUT}"}]) == ""
    again, warning = pr._load_presets()
    assert not warning and again[-1]["name"] == "Mine"


# ── Format Converter ────────────────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_format_converter_batch(ui, media, outdir):
    ui.reset()
    tab = ui.open("Format Converter")
    tab._clear()
    for key in ("hevc10", "vp9", "mp3"):
        tab.load_file(media[key])
    from tabs.transcoder.converter import FORMATS

    tab.fmt_var.set(FORMATS["mp4"][0])
    tab.out_dir_var.set(str(outdir))
    tab._render()
    ui.wait_idle(300)
    _ok(ui)
    outs = sorted(os.listdir(outdir))
    assert len([f for f in outs if f.endswith(".mp4")]) == 3, outs
    for f in outs:
        info = ff.probe(str(outdir / f), use_cache=False)
        assert info.has_audio and info.require_audio().codec in ("aac", "mp3")
        if info.has_video:
            assert info.require_video().codec in ("h264", "hevc")


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_format_converter_reports_failures(ui, media, outdir, tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video at all" * 100)
    ui.reset()
    tab = ui.open("Format Converter")
    tab._clear()
    tab.load_file(media["h264"])
    tab.load_file(str(bad))
    from tabs.transcoder.converter import FORMATS

    tab.fmt_var.set(FORMATS["mkv"][0])
    tab.out_dir_var.set(str(outdir))
    tab._render()
    ui.wait_idle(120)
    errs = ui.dialogs.errors()
    assert errs and "1 of 2" in errs[0][2]
    ui.dialogs.reset()
    assert [f for f in os.listdir(outdir) if f.endswith(".mkv")] and not [
        f for f in os.listdir(outdir) if ".part" in f
    ]


# ── Downsizer ───────────────────────────────────────────────────────────────


def test_downsizer_plan():
    from core.ops.downsizer import Goal, plan, retry_kbps

    p = plan(Goal(10_000_000, codec="h264"), 60, (1920, 1080), 60)
    assert (
        p.fps == 30 and p.height < 1080 and p.video_kbps + p.audio_kbps < 10_000_000 * 8 / 1000 / 60
    )
    with pytest.raises(ValueError):
        plan(Goal(8_000_000), 3600, (1920, 1080), 30)
    assert retry_kbps(1000, 10_000_000, 11_000_000) < 1000


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("speed", ["quality", "fast"])
def test_downsizer_hits_target(ui, media, outdir, speed):
    ui.reset()
    tab = ui.open("Universal Downsizer")
    tab.src_row.set(media["h264_1080p25"])
    ui.pump(0.4)
    from tabs.transcoder.downsizer import TARGETS

    tab.target_var.set(TARGETS[-1][0])  # custom
    tab._target_mb.set("0.5")
    tab.speed_var.set(speed)
    out = str(outdir / f"small_{speed}.mp4")
    tab.out_row.set(out)
    tab._start_render()
    ui.wait_idle(300)
    _ok(ui)
    assert os.path.getsize(out) <= 500_000
    info = ff.probe(out, use_cache=False)
    assert info.has_video and info.duration == pytest.approx(
        ff.duration_of(media["h264_1080p25"]), abs=0.2
    )


# ── Resolution Scaler / Framerate Interpolator ──────────────────────────────


def test_scaler_helpers():
    from fractions import Fraction as F

    from tabs.transcoder.scaler import chunk_step, chunks, geometry

    assert chunk_step(F(30000, 1001), F(60000, 1001)) == F(1001, 30000)
    assert chunk_step(F(25), F(60)) == F(1, 5)
    pieces = chunks(10.0, F(1, 5), 4)
    assert len(pieces) == 4 and abs(sum(p[1] for p in pieces) - 10.0) < 1e-9
    assert all(abs(round(a * 5) - a * 5) < 1e-9 for a, _ in pieces)
    assert geometry((1920, 1080), (1280, 720), "fit") == ((1280, 720), (1280, 720))
    assert geometry((1440, 1080), (1920, 1080), "pad") == ((1440, 1080), (1920, 1080))


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "size,shape,want",
    [("Twice the size (×2)", "fit", (2560, 1440)), ("480p (854×480)", "pad", (854, 480))],
)
def test_resolution_scaler(ui, media, outdir, size, shape, want):
    from tabs.transcoder.scaler import SHAPES

    ui.reset()
    tab = ui.open("Resolution Scaler")
    tab.src_row.set(media["h264"])
    ui.pump(0.3)
    tab.size_var.set(size)
    tab.shape_var.set(SHAPES[shape])
    out = str(outdir / f"scaled_{shape}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    v = ff.probe(out, use_cache=False).require_video()
    assert (v.width, v.height) == want


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("slowmo", [False, True])
def test_fps_interpolator(ui, media, outdir, slowmo):
    ui.reset()
    tab = ui.open("Framerate Interpolator")
    tab.src_row.set(media["h264"])
    ui.pump(0.3)
    tab.fps_var.set("60")
    tab.slowmo_var.set(slowmo)
    tab.factor_var.set("2")
    out = str(outdir / f"interp_{slowmo}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(600)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    src_dur = ff.duration_of(media["h264"])
    assert info.require_video().fps == pytest.approx(60, abs=0.1)
    assert info.duration == pytest.approx(src_dur * (2 if slowmo else 1), abs=0.15)
    if info.has_audio:
        assert (
            info.require_audio().duration == pytest.approx(info.duration, abs=0.2)
            if info.require_audio().duration
            else True
        )


# ── Proxy Generator / Encode Queue ──────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_proxy_generator(ui, media, tmp_path):
    src = tmp_path / "A001_C002.mp4"
    shutil.copyfile(media["h264_1080p25"], src)
    ui.reset()
    tab = ui.open("Proxy Generator")
    tab._clear()
    tab.load_file(str(src))
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    out = tmp_path / "Proxy" / "A001_C002.mov"
    info = ff.probe(str(out), use_cache=False)
    assert info.require_video().codec == "prores" and (
        info.require_video().width,
        info.require_video().height,
    ) == (960, 540)
    assert info.require_audio().codec == "pcm_s16le"


@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "vcodec,acodec,ext",
    [
        ("libx264", "aac", ".mp4"),
        ("libx265", "aac", ".mp4"),
        ("libvpx-vp9", "libopus", ".webm"),
        ("libvpx", "libvorbis", ".webm"),
        ("prores_ks", "pcm_s16le", ".mov"),
        ("dnxhd", "pcm_s16le", ".mov"),
    ],
)
def test_encode_queue_commands_run(media, tmp_path, vcodec, acodec, ext):
    from tabs.transcoder.encode_queue import Job

    j = Job()
    j.src = str(tmp_path / "in.mp4")
    import subprocess

    subprocess.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-t",
            "1",
            "-i",
            media["h264"],
            "-c",
            "copy",
            j.src,
        ],
        check=True,
    )
    j.out = str(tmp_path / f"out{ext}")
    j.vcodec, j.acodec, j.resolution, j.fps = vcodec, acodec, "640x360", "29.97"
    cmd = j.build_cmd(ff.ffmpeg_path())
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    v = ff.probe(j.out, use_cache=False).require_video()
    assert (v.width, v.height) == (640, 360) and v.fps == pytest.approx(29.97, abs=0.01)


def test_encode_queue_rejects_bad_combinations():
    from tabs.transcoder.encode_queue import Job

    j = Job()
    j.src, j.out = "a.mp4", "b.mp4"
    j.vcodec = "libvpx"
    with pytest.raises(ValueError):
        j.build_cmd("ffmpeg")
    j.vcodec, j.resolution = "copy", "1280x720"
    with pytest.raises(ValueError):
        j.build_cmd("ffmpeg")
