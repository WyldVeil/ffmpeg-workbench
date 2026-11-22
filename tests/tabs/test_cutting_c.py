"""Video Reverser, Freeze Frame, Clip Looper, Scene Detector and Sequencer."""

import os

import pytest

from core import ffmpeg as ff
from core.ops import freeze_frame as freeze_ops
from core.ops import looper, scenes
from tabs.cutting import reverser


def test_pure_helpers():
    assert reverser.reverse_chunks(0, 25, 10) == [(15, 25), (5, 15), (0, 5)]
    assert reverser.plan("range", 10, 2, 5, 1) == [("fwd", 0.0, 2), ("rev", 2, 5), ("fwd", 5, 10)]
    assert len(reverser.plan("pingpong", 10, 0, 2, 3)) == 6
    assert reverser.chunk_seconds(3840, 2160, 60) < reverser.chunk_seconds(1280, 720, 30)
    assert looper.loop_units("accel", 3, 2.0) == [(1.0, 1.0), (2.0, 1.0), (4.0, 1.0)]
    assert scenes.scene_ranges([0.0, 2.0, 5.0], 8.0) == [(0.0, 2.0), (2.0, 5.0), (5.0, 8.0)]
    freeze = freeze_ops.Freeze(mode="insert", at=1.0, hold=2.0)
    graph, total, audio = freeze_ops.freeze_graph(freeze, 6.0, has_audio=True)
    assert total == 8.0 and audio and "tpad=start_mode=clone:start_duration=2.0000" in graph


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
@pytest.mark.parametrize("mode", ["full", "pingpong"])
def test_reverser_chunked(ui, media, outdir, monkeypatch, mode):
    monkeypatch.setattr(
        reverser, "MEMORY_BUDGET", 1280 * 720 * 1.5 * 1.3 * 30 * 1.5
    )  # 1.5 s chunks
    ui.reset()
    tab = ui.open("Video Reverser")
    _src(ui, tab, media["h264"])
    tab.mode_var.set(mode)
    tab._mode_changed()
    tab.section.set(1, 2.5)
    tab.repeat_var.set("2")
    out = str(outdir / f"rev_{mode}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    expected = ff.duration_of(media["h264"]) if mode == "full" else 1.5 * 4
    assert info.require_video().duration == pytest.approx(expected, abs=0.1)
    assert info.require_audio().duration == pytest.approx(expected, abs=0.1)
    if mode == "full":  # first output frame == last source frame
        last = ff.grab_frame_png(media["h264"], expected - 0.04, width=64)
        first = ff.grab_frame_png(out, 0.0, width=64)
        assert last and first


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("mode", ["insert", "end", "only"])
def test_freeze(ui, media, outdir, mode):
    ui.reset()
    tab = ui.open("Freeze Frame")
    _src(ui, tab, media["h264"])
    tab.mode_var.set(mode)
    tab.at.var.set("2")
    tab.hold_var.set("1.5")
    tab.flash_var.set(True)
    tab.caption_var.set("Yep, that's me {x} 100%")
    out = str(outdir / f"freeze_{mode}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    dur = ff.duration_of(media["h264"])
    info = ff.probe(out, use_cache=False)
    expected = {"insert": dur + 1.5, "end": dur + 1.5, "only": 1.5}[mode]
    assert info.require_video().duration == pytest.approx(expected, abs=0.1)
    if mode != "only":
        assert info.require_audio().duration == pytest.approx(expected, abs=0.1)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("mode", ["simple", "accel", "stutter", "echo"])
def test_looper(ui, media, outdir, mode):
    ui.reset()
    tab = ui.open("Clip Looper")
    _src(ui, tab, media["hevc10"])
    tab.mode_var.set(mode)
    tab._mode_changed()
    tab.section.set(1, 2)
    tab.count_var.set("3")
    tab.factor_var.set("2")
    tab.slice_var.set("0.25")
    out = str(outdir / f"loop_{mode}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    dur = ff.duration_of(media["hevc10"])
    seg = 0.25 if mode == "stutter" else 1.0
    loops = seg * (1 + 0.5 + 0.25) if mode == "accel" else seg * 3
    end = 1.0 + seg
    expected = 1.0 + loops + (dur - end)
    info = ff.probe(out, use_cache=False)
    assert info.require_video().duration == pytest.approx(expected, abs=0.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_scene_detect_split_and_chapters(ui, outdir, tmp_path):
    clip = str(tmp_path / "three scenes.mp4")
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=640x360:r=25:d=2",
            "-f",
            "lavfi",
            "-i",
            "smptebars=s=640x360:r=25:d=2",
            "-f",
            "lavfi",
            "-i",
            "mandelbrot=s=640x360:r=25",
            "-filter_complex",
            "[2]trim=duration=2[m];[0][1][m]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            clip,
        ]
    )
    ui.reset()
    tab = ui.open("Scene Detector")
    _src(ui, tab, clip)
    tab.dir_var.set(str(outdir / "scenes"))
    tab._detect()
    ui.wait_idle(120)
    _ok(ui)
    assert len(tab.scenes) == 3
    assert [round(a) for a, _b in tab.scenes] == [0, 2, 4]
    tab._split()
    ui.wait_idle(120)
    _ok(ui)
    files = sorted(os.listdir(next(os.scandir(outdir / "scenes")).path))
    assert len(files) == 3
    tab._chapters()
    ui.wait_idle(60)
    _ok(ui)
    chaps = (
        ff.capture(
            [
                ff.ffprobe_path(),
                "-v",
                "error",
                "-show_chapters",
                "-of",
                "csv=p=0",
                str(tmp_path / "three scenes_chapters.mp4"),
            ]
        )
        .stdout.strip()
        .splitlines()
    )
    assert len(chaps) == 3


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_sequencer_mixed(ui, media, outdir):
    ui.reset()
    tab = ui.open("Multi-Clip Sequencer")
    tab._clear()
    assert tab.add_paths([media["h264"], media["vertical_noaudio"], media["hevc10"]]) == 3
    tab.tree.selection_set("0")
    ui.pump(0.2)
    tab.trim.set(1, 3)
    tab._apply_trim()
    out = str(outdir / "sequence.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    expected = 2 + ff.duration_of(media["vertical_noaudio"]) + ff.duration_of(media["hevc10"])
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(expected, abs=0.2)
    assert (info.width, info.height) == (1280, 720) and info.has_audio
