"""Snapshot (save a frame as a picture) and , / . frame stepping."""

from __future__ import annotations

import os
import subprocess

import pytest

from core import ffmpeg as ff
from core.ops import snapshot
from tabs import framestep
from tabs.social.snapshot import SHARPEN, SIZES


def _make(src, out, *args):
    r = subprocess.run(
        [
            ff.ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            src,
            "-t",
            "1",
            *args,
            "-an",
            out,
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return ff.probe(out, use_cache=False)


def test_snap():
    assert framestep.snap(1.0, 30) == 1.0
    assert framestep.snap(1.01, 30, 1) == pytest.approx(31 / 30)
    assert framestep.snap(0.0, 25, -1) == pytest.approx(-1 / 25)
    assert framestep.snap(2.0, 29.97, 0) == pytest.approx(60 / 29.97)


def test_frame_name():
    assert snapshot.frame_name("/x/my clip.mp4", 62.3456) == "my clip 00-01-02.346.png"
    assert snapshot.frame_name("a.mkv", 3725.0) == "a 01-02-05.000.png"


@pytest.mark.ffmpeg
def test_chain_defaults(media):
    info = ff.probe(media["h264"])
    chain = snapshot.build_chain(info)
    assert "in_color_matrix=bt709" in chain and "out_range=pc" in chain  # HD matrix, full range
    assert "full_chroma_int" in chain and chain.endswith("format=rgb24")
    assert "scale=1280:720" in chain
    assert "bwdif" not in chain and "zscale" not in chain and "cas" not in chain
    plain = snapshot.build_chain(
        info,
        snapshot.Picture(
            accurate=False, size="double", sharpen=0.6, deep=True, brightness=0.1, denoise=4
        ),
    )
    assert "scale=2560:1440,format=gbrp16le" in plain and "cas=strength=0.6" in plain
    assert "eq=brightness=0.1" in plain and "hqdn3d" in plain and plain.endswith("format=rgb48be")


@pytest.mark.ffmpeg
def test_chain_handles_interlaced_anamorphic_and_hdr(media, tmp_path):
    inter = _make(
        media["h264"],
        str(tmp_path / "i.mp4"),
        "-vf",
        "scale=720:576,setsar=64/45",
        "-flags",
        "+ilme+ildct",
        "-field_order",
        "tt",
        "-c:v",
        "libx264",
        "-x264-params",
        "interlaced=1",
    )
    chain = snapshot.build_chain(inter)
    assert "bwdif" in chain
    assert "scale=1024:576" in chain  # square pixels
    assert "scale=720:576" in snapshot.build_chain(
        inter, snapshot.Picture(square_pixels=False, deinterlace=False)
    )
    assert "in_color_matrix=bt601" in chain  # SD, untagged
    pq = _make(
        media["h264"],
        str(tmp_path / "pq.mp4"),
        "-c:v",
        "libx265",
        "-pix_fmt",
        "yuv420p10le",
        "-x265-params",
        "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "smpte2084",
        "-colorspace",
        "bt2020nc",
    )
    assert pq.is_hdr
    chain = snapshot.build_chain(pq)
    assert "tonemap" in chain and "in_color_matrix=bt709" in chain  # tone-mapped to SDR first
    assert "tonemap" not in snapshot.build_chain(pq, snapshot.Picture(tonemap=False))
    out = str(tmp_path / "pq.png")
    r = subprocess.run(
        snapshot.save_command(pq.path, 0.2, chain, out), capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


def _rgb(path_or_cmd) -> bytes:
    r = subprocess.run(path_or_cmd, capture_output=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_snapshot_saves_the_exact_frame(ui, media, outdir):
    ui.reset()
    tab = ui.open("Snapshot")
    assert tab.load_file(media["h264"])
    ui.pump(0.5)
    framestep.touch(tab.slider)
    for _ in range(45):  # , and . on the main window, as a user would press them
        framestep.on_key(ui.app, None, 1)
    framestep.on_key(ui.app, None, -1, second=True)  # Shift+, : one second back
    framestep.on_key(ui.app, None, 1, second=True)
    assert tab._frame() == 45
    ui.pump(0.3)
    out = str(outdir / "shot.png")
    tab._save(out)
    ui.wait_for(
        lambda: "Saved" in tab.result_lbl.cget("text") or "Couldn't" in tab.result_lbl.cget("text"),
        timeout=60,
    )
    assert "Saved" in tab.result_lbl.cget("text"), tab.result_lbl.cget("text")
    info = ff.probe(out, use_cache=False)
    assert (info.require_video().width, info.require_video().height) == (
        1280,
        720,
    ) and info.require_video().pix_fmt == "rgb24"
    # the very same frame (number 45, counted from 0) straight from FFmpeg
    raw = [ff.ffmpeg_path(), "-hide_banner", "-loglevel", "error"]
    want = _rgb(
        [
            *raw,
            "-i",
            media["h264"],
            "-vf",
            f"select=eq(n\\,45),{tab.chain()}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    got = _rgb([*raw, "-i", out, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    assert got == want
    before = _rgb(
        [
            *raw,
            "-i",
            media["h264"],
            "-vf",
            f"select=eq(n\\,44),{tab.chain()}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    assert got != before  # the frames do differ, so the check means something


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_snapshot_options_and_quick_save(ui, media, outdir):
    ui.reset()
    tab = ui.open("Snapshot")
    tab.load_file(media["hevc10"])
    ui.pump(0.3)
    assert tab.deep_var.get()  # 10-bit source -> 16-bit PNG by default
    tab.size_var.set(SIZES["half"])
    tab.sharpen_var.set(SHARPEN[0.3])
    tab._options_changed()
    ui.pump(0.5)
    assert "cas=strength=0.3" in tab.chain() and "16-bit" in tab.size_lbl.cget("text")
    tab._last_dir = str(outdir)
    for _ in range(2):  # a second quick save gets its own name
        tab._quick_save()
        ui.wait_for(lambda: "Saved" in tab.result_lbl.cget("text"), timeout=60)
        tab.result_lbl.config(text="")
    pngs = sorted(p for p in os.listdir(outdir) if p.endswith(".png"))
    first = [p for p in pngs if not p.endswith(" (2).png")]
    assert len(pngs) == 2 and len(first) == 1, pngs
    info = ff.probe(str(outdir / first[0]), use_cache=False)
    assert info.require_video().pix_fmt == "rgb48be"
    src = ff.probe(media["hevc10"])
    assert info.require_video().width == src.require_video().width // 2 // 2 * 2


@pytest.mark.gui
def test_keys_leave_text_boxes_alone(ui, media):
    ui.reset()
    tab = ui.open("Snapshot")
    tab.load_file(media["h264"])
    ui.pump(0.3)
    framestep.touch(tab.slider)
    import tkinter as tk

    entry = next(w for w in ui.walk(tab) if isinstance(w, tk.Entry))
    real = ui.app.focus_get
    try:
        ui.app.focus_get = lambda: entry  # the cursor is in a text box
        assert framestep.on_key(ui.app, None, 1) is None  # typing a full stop must still work
        assert tab._frame() == 0
        ui.app.focus_get = lambda: tab.slider
    finally:
        pass
    try:
        assert framestep.on_key(ui.app, None, 1) == "break" and tab._frame() == 1
    finally:
        ui.app.focus_get = real


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_timeline_handles_step_by_frame(ui, media):
    ui.reset()
    tab = ui.open("Manual Multi-Splitter")
    tab.load_file(media["h264"])  # dropping a file loads the timeline too
    ui.pump(0.3)
    tl = tab._timeline
    assert tl._duration == pytest.approx(6.0, abs=0.1) and tl.fps == pytest.approx(30)
    tl._last_moved = "start"
    framestep.touch(tl._canvas)
    for _ in range(3):
        framestep.on_key(ui.app, None, 1)
    tl._last_moved = "end"
    framestep.on_key(ui.app, None, -1, second=True)
    start, end = tl.get_range()
    assert start == pytest.approx(0.1) and end == pytest.approx(tl._duration - 1.0, abs=1 / 30)
    tab._add_row()
    row = tab.split_rows[-1]
    assert row["start"].get() == "00:00:00.100"  # the new range comes from the timeline
    assert ff.parse_time(row["end"].get()) == pytest.approx(end, abs=0.001)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_gif_maker_frame_steps_then_i_and_o(ui, media):
    ui.reset()
    tab = ui.open("Pro GIF Maker")
    tab.load_file(media["h264"])
    ui.pump(0.5)
    tab.slider.focus_force()
    framestep.touch(tab.slider)
    ui.pump(0.1)
    framestep.on_key(ui.app, None, 1, second=True)  # Shift+. : one second
    ui.app.shortcuts.mark_in()  # i
    for _ in range(15):
        framestep.on_key(ui.app, None, 1)  # .
    ui.app.shortcuts.mark_out()  # o
    assert ff.parse_time(tab.start_var.get()) == pytest.approx(1.0)
    assert float(tab.dur_var.get()) == pytest.approx(0.5)


@pytest.mark.gui
@pytest.mark.parametrize(
    "tool,expected",
    [
        ("Special Effects", 10),
        ("Universal Downsizer", 2),
        ("WebM Maker", 2),
        ("All-in-One Builder", 4),
    ],
)
def test_frame_pickers_next_to_time_fields(ui, tool, expected):
    """Tools without a preview get a 🎞 button next to each start / end time."""
    import tkinter as tk

    ui.reset()
    tab = ui.open(tool)
    found = sum(1 for w in ui.walk(tab) if isinstance(w, tk.Button) and w.cget("text") == "🎞")
    assert found == expected


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_frame_picker_fills_the_field(ui, media):
    import tkinter as tk

    from tabs.widgets import FramePicker

    var = tk.StringVar(value="1.0")
    picker = FramePicker(ui.app, media["h264"], var)
    ui.pump(0.3)
    assert float(picker.pos.get()) == pytest.approx(1.0)
    for _ in range(3):
        picker.event_generate("<period>")
    picker.event_generate("<less>")  # Shift+, : a second back
    ui.pump(0.2)
    picker._use()
    assert ff.parse_time(var.get()) == pytest.approx(0.1)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "tool",
    [
        "Privacy Blur",
        "Image Watermark",
        "Picture-in-Picture",
        "Animated Zoom",
        "Animated Titles",
        "Chapter Markers",
        "Clip Looper",
        "The Muter",
    ],
)
def test_i_and_o_use_the_frame_shown(ui, media, tool):
    ui.reset()
    tab = ui.open(tool)
    tab.load_file(media["h264"])
    ui.pump(0.6)
    assert hasattr(tab, "mark_in"), tool
    before = {
        k: v.get() for k, v in vars(tab).items() if isinstance(v, __import__("tkinter").Variable)
    }
    target = next(w for w in framestep._order if w() is not None and framestep._inside(w(), tab))()
    framestep.touch(target)
    start = float(target.get())  # previews start part-way in
    want = framestep.snap(start, 30, 36)  # + 1 s + 6 frames at 30 fps
    framestep.on_key(ui.app, None, 1, second=True)
    for _ in range(6):
        framestep.on_key(ui.app, None, 1)
    ui.app.shortcuts.mark_in()
    ui.pump(0.2)
    after = {
        k: v.get() for k, v in vars(tab).items() if isinstance(v, __import__("tkinter").Variable)
    }
    changed = [k for k in after if after[k] != before.get(k)]
    if tool == "Chapter Markers":
        assert any(abs(c.time - want) < 0.01 for c in tab.chapters), tab.chapters
    elif tool == "Animated Titles":
        assert ff.parse_time(tab._card()["vars"]["start"].get()) == pytest.approx(want)
    elif tool == "Animated Zoom":
        assert ff.parse_time(tab.vars["p_start"].get()) == pytest.approx(want)
    else:
        assert any(ff.format_time(want) in str(after[k]) for k in changed), (want, changed, after)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "tool,attr",
    [
        ("Thumbnail Maker", "duration"),
        ("Chapter Markers", "_duration"),
        ("Laugh Track Remover", "duration"),
    ],
)
def test_dropped_file_is_fully_loaded(ui, media, tool, attr):
    """Drag and drop / File > Open (the shared load_file) must load the file,
    not just fill in its name."""
    ui.reset()
    tab = ui.open(tool)
    assert tab.load_file(media["h264"])
    ui.pump(0.8)
    assert getattr(tab, attr, 0) == pytest.approx(6.0, abs=0.2), (tool, getattr(tab, attr, None))


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_splitter_renumbers_clips(ui, media):
    ui.reset()
    tab = ui.open("Manual Multi-Splitter")
    tab.load_file(media["h264"])
    ui.pump(0.3)
    for r in list(tab.split_rows):  # the tab is reused between tests
        tab._remove_row(r["frame"])
    for a, b in ((5.0, 5.5), (0.0, 1.0), (2.0, 3.0)):
        tab._timeline.set_range(a, b)
        tab._add_row()
    tab._remove_row(tab.split_rows[0]["frame"])
    assert [r["label"].cget("text") for r in tab.split_rows] == ["Clip 1:", "Clip 2:"]
    assert [r["suffix"].get() for r in tab.split_rows] == ["_clip1", "_clip2"]
    assert tab.split_rows[0]["start"].get() == "00:00:00.000"
