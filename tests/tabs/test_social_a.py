"""Shortifier, Auto-Cropper, GIF Maker, Watermarker and Image Watermark."""

import os

import pytest

from core import ffmpeg as ff
from core.ops import gif_maker, image_watermark, text_watermark
from core.ops import shortifier as shortifier_ops
from tabs.social import shortifier


def test_pure_helpers():
    assert text_watermark.parse_ranges("0-5, 1:00-1:10") == [(0.0, 5.0), (60.0, 70.0)]
    with pytest.raises(ValueError):
        text_watermark.parse_ranges("5-2")
    segs = text_watermark.bounce_path(10, 1920, 1080, 300, 60, 200)
    assert segs[-1][1] == pytest.approx(10, abs=0.01)
    assert all(-1 <= x <= 1920 - 300 + 1 for s in segs for x in (s[2], s[4]))
    g = shortifier_ops.reframe_graph(1080, 1920, "crop", "left")
    assert "x=(iw-ow)*0.0" in g
    assert "paletteuse=dither=bayer:bayer_scale=4" in gif_maker.gif_graph(15, 480, 128, "bayer4")
    assert "scale=192:-1" in image_watermark.logo_graph(
        1280, 720, image_watermark.Logo(pct=15, opacity=1, place="br")
    )


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


def _ok(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


def _src(ui, tab, path):
    tab.src_var.set(path)
    ui.pump(0.6)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("layout", ["crop", "blur", "mirror", "colour"])
def test_shortifier(ui, media, outdir, layout):
    ui.reset()
    tab = ui.open("The Shortifier")
    _src(ui, tab, media["h264"])
    tab.bg_var.set(shortifier.BACKGROUNDS[layout])
    out = str(outdir / f"short_{layout}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.require_video().display_size == (1080, 1920) and info.has_audio


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_auto_cropper(ui, media, outdir):
    boxed = str(outdir / "boxed.mp4")
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            media["hevc10"],
            "-vf",
            "scale=960:540,pad=1280:720:160:90",
            "-c:v",
            "libx265",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p10le",
            "-c:a",
            "copy",
            boxed,
        ]
    )
    ui.reset()
    tab = ui.open("Auto-Cropper")
    _src(ui, tab, boxed)
    ui.wait_for(lambda: tab.box is not None, 60)
    assert tab.box == (160, 90, 960, 540)
    out = str(outdir / "nobars.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    assert ff.probe(out, use_cache=False).require_video().display_size == (960, 540)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("fmt,limit", [("GIF", ""), ("GIF", "0.4"), ("Animated WebP", "")])
def test_gif(ui, media, outdir, fmt, limit):
    ui.reset()
    tab = ui.open("Pro GIF Maker")
    _src(ui, tab, media["h264"])
    tab.format_var.set(fmt)
    tab.limit_var.set(limit)
    tab.dur_var.set("2")
    ext = ".webp" if "WebP" in fmt else ".gif"
    out = str(outdir / f"anim_{bool(limit)}{ext}")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    assert os.path.isfile(out)
    if limit:
        assert os.path.getsize(out) <= 0.4 * 1024 * 1024
    info = ff.probe(out, use_cache=False)
    assert info.require_video().codec.startswith("webp" if ext == ".webp" else "gif")


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("place", ["br", "bounce", "jump"])
def test_text_watermark(ui, media, outdir, place):
    ui.reset()
    tab = ui.open("Watermarker")
    _src(ui, tab, media["vertical_noaudio"])
    tab.text_var.set("O'Brien {x} 50% \\o/")
    tab.place_var.set(text_watermark.PLACES[place])
    tab.always_var.set(place != "jump")
    tab.ranges_var.set("0-1.5")
    out = str(outdir / f"wm_{place}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    ui.assert_media(out, video=True, min_duration=1.5)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_logo_watermark(ui, media, outdir, tmp_path):
    logo = str(tmp_path / "logo.png")
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red@0.5:s=300x120,format=rgba",
            "-frames:v",
            "1",
            logo,
        ]
    )
    ui.reset()
    tab = ui.open("Image Watermark")
    _src(ui, tab, media["h264"])
    tab.logo_row.set(logo)
    tab.window_var.set(True)
    tab.from_var.set("1")
    tab.to_var.set("3")
    out = str(outdir / "logo.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(ff.duration_of(media["h264"]), abs=0.1)
