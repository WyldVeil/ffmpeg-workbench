"""Snapshot (save a frame as a picture) and , / . frame stepping."""
from __future__ import annotations

import os
import subprocess

import pytest

from core import ffmpeg as ff
from tabs import framestep
from tabs.social import snapshot


def _make(src, out, *args):
    r = subprocess.run([ff.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-t", "1",
                        *args, "-an", out], capture_output=True, text=True)
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
    assert "in_color_matrix=bt709" in chain and "out_range=pc" in chain      # HD matrix, full range
    assert "full_chroma_int" in chain and chain.endswith("format=rgb24")
    assert "scale=1280:720" in chain
    assert "bwdif" not in chain and "zscale" not in chain and "cas" not in chain
    plain = snapshot.build_chain(info, accurate=False, size="double", sharpen=0.6, deep=True,
                                 brightness=0.1, denoise=4)
    assert "scale=2560:1440,format=gbrp16le" in plain and "cas=strength=0.6" in plain
    assert "eq=brightness=0.1" in plain and "hqdn3d" in plain and plain.endswith("format=rgb48be")


@pytest.mark.ffmpeg
def test_chain_handles_interlaced_anamorphic_and_hdr(media, tmp_path):
    inter = _make(media["h264"], str(tmp_path / "i.mp4"), "-vf", "scale=720:576,setsar=64/45", "-flags",
                  "+ilme+ildct", "-field_order", "tt", "-c:v", "libx264", "-x264-params", "interlaced=1")
    chain = snapshot.build_chain(inter)
    assert "bwdif" in chain
    assert "scale=1024:576" in chain                                            # square pixels
    assert "scale=720:576" in snapshot.build_chain(inter, square_pixels=False, deinterlace=False)
    assert "in_color_matrix=bt601" in chain                                     # SD, untagged
    pq = _make(media["h264"], str(tmp_path / "pq.mp4"), "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
               "-x265-params", "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
               "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc")
    assert pq.is_hdr
    chain = snapshot.build_chain(pq)
    assert "tonemap" in chain and "in_color_matrix=bt709" in chain             # tone-mapped to SDR first
    assert "tonemap" not in snapshot.build_chain(pq, tonemap=False)
    out = str(tmp_path / "pq.png")
    r = subprocess.run(snapshot.save_command(pq.path, 0.2, chain, out), capture_output=True, text=True)
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
    for _ in range(45):                       # , and . on the main window, as a user would press them
        framestep.on_key(ui.app, None, 1)
    framestep.on_key(ui.app, None, -1, second=True)            # Shift+, : one second back
    framestep.on_key(ui.app, None, 1, second=True)
    assert tab._frame() == 45
    ui.pump(0.3)
    out = str(outdir / "shot.png")
    tab._save(out)
    ui.wait_for(lambda: "Saved" in tab.result_lbl.cget("text") or "Couldn't" in tab.result_lbl.cget("text"),
                timeout=60)
    assert "Saved" in tab.result_lbl.cget("text"), tab.result_lbl.cget("text")
    info = ff.probe(out, use_cache=False)
    assert (info.video.width, info.video.height) == (1280, 720) and info.video.pix_fmt == "rgb24"
    # the very same frame (number 45, counted from 0) straight from FFmpeg
    raw = [ff.ffmpeg_path(), "-hide_banner", "-loglevel", "error"]
    want = _rgb(raw + ["-i", media["h264"], "-vf", f"select=eq(n\\,45),{tab.chain()}", "-frames:v", "1",
                       "-f", "rawvideo", "-"])
    got = _rgb(raw + ["-i", out, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    assert got == want
    before = _rgb(raw + ["-i", media["h264"], "-vf", f"select=eq(n\\,44),{tab.chain()}", "-frames:v", "1",
                         "-f", "rawvideo", "-"])
    assert got != before                     # the frames do differ, so the check means something


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_snapshot_options_and_quick_save(ui, media, outdir):
    ui.reset()
    tab = ui.open("Snapshot")
    tab.load_file(media["hevc10"])
    ui.pump(0.3)
    assert tab.deep_var.get()                # 10-bit source -> 16-bit PNG by default
    tab.size_var.set(snapshot.SIZES["half"])
    tab.sharpen_var.set(snapshot.SHARPEN[0.3])
    tab._options_changed()
    ui.pump(0.5)
    assert "cas=strength=0.3" in tab.chain() and "16-bit" in tab.size_lbl.cget("text")
    tab._last_dir = str(outdir)
    for _ in range(2):                       # a second quick save gets its own name
        tab._quick_save()
        ui.wait_for(lambda: "Saved" in tab.result_lbl.cget("text"), timeout=60)
        tab.result_lbl.config(text="")
    pngs = sorted(p for p in os.listdir(outdir) if p.endswith(".png"))
    first = [p for p in pngs if not p.endswith(" (2).png")]
    assert len(pngs) == 2 and len(first) == 1, pngs
    info = ff.probe(str(outdir / first[0]), use_cache=False)
    assert info.video.pix_fmt == "rgb48be"
    src = ff.probe(media["hevc10"])
    assert info.video.width == src.video.width // 2 // 2 * 2


@pytest.mark.gui
def test_keys_leave_text_boxes_alone(ui, media):
    ui.reset()
    tab = ui.open("Snapshot")
    tab.load_file(media["h264"])
    ui.pump(0.3)
    framestep.touch(tab.slider)
    import tkinter as tk
    entry = next(w for w in ui.walk(tab) if isinstance(w, tk.Entry))
    entry.focus_force()
    ui.pump(0.1)
    assert framestep.on_key(ui.app, None, 1) is None       # typing a full stop must still work
    assert tab._frame() == 0
    tab.slider.focus_force()
    ui.pump(0.1)
    assert framestep.on_key(ui.app, None, 1) == "break" and tab._frame() == 1


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_timeline_handles_step_by_frame(ui, media):
    ui.reset()
    tab = ui.open("Manual Multi-Splitter")
    tab.load_file(media["h264"])                               # dropping a file loads the timeline too
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
    assert row["start"].get() == "00:00:00.100"               # the new range comes from the timeline
    assert ff.parse_time(row["end"].get()) == pytest.approx(end, abs=0.001)
