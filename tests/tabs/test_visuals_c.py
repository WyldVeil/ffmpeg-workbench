"""Visuals part C: Denoise, Sharpen, Deinterlace, Privacy Blur, Picture-in-Picture, Animated Zoom,
Transition Studio."""

import subprocess

import pytest

from core import ffmpeg as ff


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness
    return Harness.instance()


def _ok(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


def _pixel_luma(path, at, x, y):
    r = subprocess.run([ff.ffmpeg_path(), "-v", "error", "-ss", str(at), "-i", path, "-frames:v", "1",
                        "-vf", f"crop=8:8:{x}:{y},format=gray,scale=1:1", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    return r.stdout[0]


def test_graphs():
    from tabs.visuals.pip import PipSpec, pip_graph
    from tabs.visuals.region_blur import Region, blur_graph
    g = blur_graph([Region(0.1, 0.1, 0.2, 0.2, "blur"), Region(0.5, 0.5, 0.2, 0.2, "box", end=2.0)], 1280, 720)
    assert "split=2[base][c0]" in g and "drawbox=" in g and g.endswith("null[v]")
    spec = PipSpec((1920, 1080), (1280, 720), pct=25, place="br", border=4, radius=20, anim="left", start=1, end=4)
    assert spec.inset_size() == (480, 270)
    g = pip_graph(spec)
    assert "geq=" in g and "pad=488:278" in g and "between(t,1.000,4.000)" in g


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("tool,out_name", [("Denoise", "dn.mp4"), ("Sharpen", "sh.mp4")])
def test_denoise_sharpen(ui, media, outdir, tool, out_name):
    ui.reset()
    tab = ui.open(tool)
    tab.src_row.set(media["h264"])
    ui.pump(0.3)
    out = str(outdir / out_name)
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    assert ff.probe(out, use_cache=False).duration == pytest.approx(ff.duration_of(media["h264"]), abs=0.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_deinterlace_double_rate(ui, outdir, tmp_path):
    src = str(tmp_path / "interlaced.mp4")
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=720x576:r=50:d=2,tinterlace=4,setfield=tff", "-c:v", "libx264",
                    "-flags", "+ildct+ilme", "-x264-params", "tff=1", src], check=True)
    ui.reset()
    tab = ui.open("Deinterlace")
    tab.src_row.set(src)
    ui.pump(0.3)
    from tabs.visuals.deinterlace import MODES
    tab.mode_var.set(MODES["double"])
    out = str(outdir / "prog.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    v = ff.probe(out, use_cache=False).require_video()
    assert v.fps == pytest.approx(50, abs=0.1) and v.field_order in ("progressive", "")


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_privacy_blur_regions(ui, media, outdir):
    from tabs.visuals.region_blur import Region
    ui.reset()
    tab = ui.open("Privacy Blur")
    tab.src_row.set(media["h264"])
    ui.pump(0.3)
    tab.regions[:] = [Region(0.05, 0.05, 0.3, 0.3, "blur"), Region(0.6, 0.6, 0.3, 0.3, "box"),
                      Region(0.6, 0.05, 0.3, 0.3, "pixelate", start=1.0)]
    out = str(outdir / "blur.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    assert _pixel_luma(out, 1.0, 1000, 600) < 20                # inside the black box (1280x720 source)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_pip_keeps_background_length(ui, media, outdir):
    from tabs.visuals.pip import ANIMS, AUDIO
    ui.reset()
    tab = ui.open("Picture-in-Picture")
    tab.bg_row.set(media["h264_1080p25"])
    tab.fg_row.set(media["vp9"])
    ui.pump(0.4)
    tab.radius_var.set("16")
    tab.border_var.set("3")
    tab.from_var.set("0.5")
    tab.anim_var.set(ANIMS["fade"])
    tab.audio_var.set(AUDIO["mix"])
    out = str(outdir / "pip.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(ff.duration_of(media["h264_1080p25"]), abs=0.15)
    assert (info.require_video().width, info.require_video().height) == (1920, 1080) and info.has_audio


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("gpu", [False, True])
def test_animated_zoom_keeps_length(ui, media, outdir, monkeypatch, gpu):
    from core import filters as fl
    if gpu and not fl.vulkan_ok():
        pytest.skip("no Vulkan GPU")
    monkeypatch.setattr(fl, "vulkan_ok", lambda: gpu)
    ui.reset()
    tab = ui.open("Animated Zoom")
    tab.src_row.set(media["h264"])
    ui.pump(0.3)
    out = str(outdir / f"zoom_{gpu}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    src = ff.probe(media["h264"])
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(src.duration, abs=0.15)
    assert info.require_video().fps == pytest.approx(src.require_video().fps, abs=0.05)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_transition_studio_mixed_clips(ui, media, outdir):
    ui.reset()
    tab = ui.open("Transition Studio")
    tab.a_row.set(media["h264"])
    tab.b_row.set(media["vp9"])
    ui.pump(0.3)
    tab.family_var.set("Wipes")
    tab._family_changed()
    tab.dur_var.set("1.0")
    out = str(outdir / "trans.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    from tabs.cutting.crossfader import Clip
    a, b = Clip.from_info(ff.probe(media["h264"])), Clip.from_info(ff.probe(media["vp9"]))
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(a.duration + b.duration - 1.0, abs=0.2)
    assert info.has_audio
