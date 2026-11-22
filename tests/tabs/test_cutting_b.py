"""Rotate & Flip, Manual Crop, Side-by-Side, Smart Reframe and Beat Sync."""

import pytest

from core import beats as beats_core
from core import ffmpeg as ff
from core.ops import side_by_side as side_by_side_ops
from tabs.cutting import beat_sync, crop, side_by_side, smart_reframe


def test_pure_helpers():
    assert crop.fit_aspect(1920, 1080, 9, 16) == (656, 0, 606, 1080)
    assert crop.parse_cropdetect(
        "x crop=1920:800:0:140 y crop=1920:800:0:140 z crop=1920:804:0:138"
    ) == (0, 140, 1920, 800)
    assert smart_reframe.crop_box(1920, 1080, 9, 16) == (608, 1080)
    plan = beat_sync.frame_plan([0.5, 1.0, 1.5], 2.0, 30)
    assert sum(f for _s, f in plan) == 60 and [s for s, _f in plan] == [0.0, 0.5, 1.0, 1.5]
    segs = beat_sync.assign_sources(plan, [("a", 1.0), ("b", 10.0)], "sequential", 30)
    assert [s[0] for s in segs] == ["a", "b", "a", "b"]
    assert all(
        start + frames / 30 <= dur + 1e-6
        for (path, start, frames), (p2, dur) in zip(
            segs, [("a", 1.0), ("b", 10.0)] * 2, strict=False
        )
    )
    assert side_by_side_ops.cells("grid", 4, 1920, 1080)[3] == (960, 540, 960, 540)


@pytest.mark.ffmpeg
def test_beat_detection_on_click_track(tmp_path):
    song = str(tmp_path / "drums.m4a")
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc='0.8*sin(2*PI*55*t)*exp(-25*mod(t,0.5))':s=44100:d=20",
            "-c:a",
            "aac",
            song,
        ]
    )
    on = beats_core.onset_strength(beats_core.envelope(song))
    bpm = 60 * beats_core.ENV_RATE / beats_core.estimate_tempo(on)
    assert bpm == pytest.approx(120, abs=1)
    beats = beats_core.beat_grid(on, beats_core.estimate_tempo(on))
    assert beats_core.find_beats(song)[1] == pytest.approx(120, abs=1)
    assert all(min(abs(b - round(b * 2) / 2), 0.5) < 0.03 for b in beats)


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


def _ok(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("lossless", [True, False])
def test_rotate(ui, media, outdir, lossless):
    ui.reset()
    tab = ui.open("Rotate & Flip")
    tab.src_var.set(media["nasty"])
    ui.pump(0.6)
    tab.rot_var.set(90)
    tab.hflip_var.set(True)
    tab.lossless_var.set(lossless)
    tab._method_changed()
    out = str(outdir / f"rot_{lossless}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.require_video().display_size == (720, 1280) and info.has_audio


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_crop_detect_and_render(ui, media, outdir):
    boxed = str(outdir / "letterboxed.mp4")
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            media["h264"],
            "-vf",
            "scale=1280:536,pad=1280:720:0:92",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "copy",
            boxed,
        ]
    )
    ui.reset()
    tab = ui.open("Manual Crop")
    tab.src_var.set(boxed)
    ui.pump(0.6)
    tab._detect_bars()
    ui.wait_for(lambda: tab.rect()[3] < 720, 60)
    _x, y, w, h = tab.rect()
    assert (w, h) == (1280, 536) and y == 92
    out = str(outdir / "cropped.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    assert ff.probe(out, use_cache=False).require_video().display_size == (1280, 536)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_side_by_side_grid(ui, media, outdir):
    ui.reset()
    tab = ui.open("Side-by-Side")
    for row, key in zip(tab.rows, ("h264", "vertical_noaudio", "hevc10", "nasty"), strict=False):
        row.set(media[key])
    tab.layout_var.set(side_by_side.LAYOUTS["grid"])
    tab.length_var.set(side_by_side.LENGTH["loop"])
    tab.audio_var.set(side_by_side.AUDIO["mix"])
    out = str(outdir / "grid.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    longest = max(ff.duration_of(media[k]) for k in ("h264", "vertical_noaudio", "hevc10", "nasty"))
    assert info.duration == pytest.approx(longest, abs=0.15) and info.has_audio


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_smart_reframe(ui, media, outdir):
    ui.reset()
    tab = ui.open("Smart Reframe")
    tab.src_var.set(media["h264"])
    ui.pump(0.6)
    out = str(outdir / "vertical.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.require_video().display_size == (1080, 1920)
    assert info.duration == pytest.approx(ff.duration_of(media["h264"]), abs=0.1)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_beat_montage(ui, media, outdir, tmp_path):
    song = str(tmp_path / "song.m4a")
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc='0.8*sin(2*PI*55*t)*exp(-25*mod(t,0.5))':s=44100:d=8",
            "-c:a",
            "aac",
            song,
        ]
    )
    ui.reset()
    tab = ui.open("Beat Sync Cutter")
    tab._clear()
    tab.src_var.set(song)
    ui.wait_for(lambda: len(tab.beats) > 10, 60)
    for key in ("h264", "hevc10", "vertical_noaudio"):
        assert tab.load_file(media[key])
    out = str(outdir / "montage.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(300)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(8.0, abs=0.1)
    assert info.has_audio and info.require_video().display_size == (1280, 720)
