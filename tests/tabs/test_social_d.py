"""Chapter Markers, Thumbnail Maker and Video Collage."""
import os
import subprocess

import pytest

from core import ffmpeg as ff
from tabs.social import chapter_markers, collage


def test_chapter_parsing_and_rules():
    found = chapter_markers.parse_chapter_text("0:00 Intro\n1:02:03.5 - Deep dive\n(12:30) Outro")
    assert found == [(0.0, "Intro"), (3723.5, "Deep dive"), (750.0, "Outro")]
    C = chapter_markers.Chapter
    assert chapter_markers.youtube_issues([C(0, "a"), C(15, "b"), C(30, "c")], 60) == []
    assert any("10 seconds" in i for i in chapter_markers.youtube_issues([C(0, "a"), C(5, "b"), C(30, "c")], 60))
    assert any("0:00" in i for i in chapter_markers.youtube_issues([C(3, "a"), C(15, "b"), C(30, "c")], 60))


def test_collage_geometry_even():
    g, has_a = collage.collage_graph(collage.LAYOUTS["3x3"], 9, 1921, 1081, 5, "black", "30", True,
                                     "first", [True] * 9)
    assert "pad=1920:1080" in g and has_a
    g, _ = collage.collage_graph(collage.LAYOUTS["pip"], 3, 1920, 1080, 4, "black", "30", False, "mix",
                                 [True, False, True])
    assert "amix=inputs=2" in g and "overlay" in g


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness
    return Harness.instance()


def _ok(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_chapters_embed(ui, media, outdir, monkeypatch):
    ui.reset()
    tab = ui.open("Chapter Markers")
    tab.src_var.set(media["h264"])
    ui.pump(0.8)
    tab.chapters.clear()
    for secs, title in ((0, "Intro 🎬"), (2, "Middle"), (4, "End")):
        tab._add_chapter(float(secs), title)
    out = str(outdir / "with_chapters.mp4")
    monkeypatch.setattr(tab, "suggest_output", lambda *a, **k: out)
    tab._embed()
    ui.wait_idle(60)
    _ok(ui)
    chaps = ff.capture([ff.ffprobe_path(), "-v", "error", "-show_chapters", "-of", "csv=p=0", out]).stdout
    assert chaps.strip().count("\n") == 2 and "Intro" in chaps


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("fmt", ["PNG", "WebP"])
def test_thumbnail_end_and_text(ui, media, outdir, fmt):
    ui.reset()
    tab = ui.open("Thumbnail Maker")
    tab._src_var.set(media["h264"])
    ui.pump(0.8)
    tab._scrub_var.set(9999)                        # past the end: clamped to the last frame
    tab._use_text.set(True)
    tab._text_var.set("It's 50% OFF: now")
    fmts = [v for v in ("PNG", "JPEG (q95)", "WebP") if fmt in v]
    tab._fmt_var.set(next(iter(fmts)) if fmts else fmt)
    out = str(outdir / "thumb.jpg")                 # wrong extension: the format wins
    tab._out_var.set(out)
    tab._render()
    ui.wait_idle(60)
    _ok(ui)
    want = os.path.splitext(out)[0] + (".png" if fmt == "PNG" else ".webp")
    assert os.path.getsize(want) > 1000


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("layout,mode", [("2x2", "longest"), ("pip", "shortest")])
def test_collage(ui, media, outdir, layout, mode):
    ui.reset()
    tab = ui.open("Video Collage")
    tab._clear_all()
    for key in ("h264", "vertical_noaudio", "hevc10", "vp9"):
        tab.load_file(media[key])
    tab._layout_var.set(collage.LAYOUTS[layout]["label"])
    tab._length_var.set(collage.LENGTHS[mode])
    tab._loop_var.set(False)
    tab._audio_mode.set(collage.AUDIO_MODES["mix"])
    out = str(outdir / f"collage_{layout}.mp4")
    tab._out_var.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    n = collage.LAYOUTS[layout]["slots"]
    durs = [ff.duration_of(media[k]) for k in ("h264", "vertical_noaudio", "hevc10", "vp9")[:n]]
    assert info.duration == pytest.approx(max(durs) if mode == "longest" else min(durs), abs=0.15)
    assert (info.width, info.height) == (1920, 1080) and info.has_audio



def test_auto_layout_choices():
    from core import grid
    assert grid.best_layout([16 / 9] * 5, 1920, 1080, gap=8).kind == "rows 2-3"       # two big, three below
    assert grid.best_layout([16 / 9] * 4, 1920, 1080).kind == "grid 2×2"
    assert grid.best_layout([9 / 16] * 3, 1920, 1080).kind == "grid 3×1"            # portrait clips side by side
    lay = grid.best_layout([16 / 9, 9 / 16, 4 / 3], 1920, 1080, gap=4)
    for x, y, w, h in lay.rects:
        assert x >= 0 and y >= 0 and x + w <= 1920 + 2 and y + h <= 1080 + 2 and w % 2 == 0 and h % 2 == 0


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_collage_auto_loops_to_longest(ui, media, outdir):
    ui.reset()
    tab = ui.open("Video Collage")
    tab._clear_all()
    keys = ("h264", "h264_b", "vertical_noaudio", "vp9", "hevc10")
    for key in keys:
        tab.load_file(media[key])
    tab._layout_var.set(collage.LAYOUTS["auto"]["label"])
    tab._length_var.set(collage.LENGTHS["longest"])
    tab._loop_var.set(True)
    tab._mode_var.set("fit")
    tab._audio_mode.set(collage.AUDIO_MODES["mix"])
    out = str(outdir / "auto.mp4")
    tab._out_var.set(out)
    tab._render()
    ui.wait_idle(300)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(max(ff.duration_of(media[k]) for k in keys), abs=0.15)
    assert info.has_audio


def _colour_clip(path, colour, seconds):
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={colour}:s=640x360:r=25:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
                   check=True)


def _rgb_at(path, at, x, y):
    r = subprocess.run([ff.ffmpeg_path(), "-v", "error", "-ss", str(at), "-i", path, "-frames:v", "1",
                        "-vf", f"crop=4:4:{x}:{y},scale=1:1,format=rgb24", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    return tuple(r.stdout[:3])


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("end,expect_left_after", [("reflow", "blue"), ("background", "black"), ("hold", "red")])
def test_collage_end_behaviours(ui, outdir, tmp_path, end, expect_left_after):
    red, blue = str(tmp_path / "red.mp4"), str(tmp_path / "blue.mp4")
    _colour_clip(red, "red", 2)
    _colour_clip(blue, "blue", 5)
    ui.reset()
    tab = ui.open("Video Collage")
    tab._clear_all()
    tab.load_file(red)
    tab.load_file(blue)
    tab._layout_var.set(collage.LAYOUTS["auto"]["label"])
    tab._length_var.set(collage.LENGTHS["longest"])
    tab._mode_var.set("fit")
    tab._loop_var.set(False)
    tab._end_var.set(collage.ENDS[end])
    tab._pad_var.set("0")
    out = str(outdir / f"end_{end}.mp4")
    tab._out_var.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    assert ff.probe(out, use_cache=False).duration == pytest.approx(5.0, abs=0.15)
    left_before = _rgb_at(out, 1.0, 400, 540)            # the left clip's area, mid-height
    assert left_before[0] > 180 and left_before[2] < 80  # red
    after = _rgb_at(out, 4.0, 400, 540)
    if expect_left_after == "blue":
        assert after[2] > 180 and after[0] < 80
    elif expect_left_after == "black":
        assert max(after) < 40
    else:
        assert after[0] > 180 and after[2] < 80


@pytest.mark.parametrize("seed", range(40))
def test_auto_layout_any_canvas_any_clips(seed):
    """Random canvas sizes and clip shapes: rectangles stay inside, never overlap, even sizes."""
    import random

    from core import grid
    rnd = random.Random(seed)
    W, H = rnd.randrange(200, 8192, 2), rnd.randrange(200, 8192, 2)
    shapes = [rnd.choice([16 / 9, 9 / 16, 4 / 3, 3 / 4, 1.0, 21 / 9, 2.39, 1.9, rnd.uniform(0.3, 3.5)])
              for _ in range(rnd.randint(1, 25))]
    gap = rnd.choice([0, 2, 8, 20])
    for mode in ("fit", "fill"):
        lay = grid.best_layout(shapes, W, H, gap=gap, mode=mode)
        assert len(lay.rects) == len(shapes)
        for x, y, w, h in lay.rects:
            assert w % 2 == 0 and h % 2 == 0 and w >= 2 and h >= 2
            assert x >= 0 and y >= 0 and x + w <= W + 2 and y + h <= H + 2
        boxes = sorted(lay.rects)
        for i, (x1, y1, w1, h1) in enumerate(boxes):
            for x2, y2, w2, h2 in boxes[i + 1:]:
                overlap_w = min(x1 + w1, x2 + w2) - max(x1, x2)
                overlap_h = min(y1 + h1, y2 + h2) - max(y1, y2)
                assert overlap_w <= 2 or overlap_h <= 2          # at most rounding slivers
        if mode == "fit":                                        # whole clips: shapes kept (within rounding)
            for (_x, _y, w, h), a in zip(lay.rects, shapes, strict=False):
                assert abs(w / h - a) / a < 0.08 or min(w, h) < 40


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_collage_odd_custom_canvas(ui, media, outdir):
    ui.reset()
    tab = ui.open("Video Collage")
    tab._clear_all()
    for key in ("vertical_noaudio", "vp9", "h264_1080p25"):          # 1080x1920, 640x360, 1920x1080
        tab.load_file(media[key])
    tab._layout_var.set(collage.LAYOUTS["auto"]["label"])
    tab._length_var.set(collage.LENGTHS["shortest"])
    tab._mode_var.set("fill")
    tab._out_w.set("1001")
    tab._out_h.set("777")
    out = str(outdir / "odd.mp4")
    tab._out_var.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    v = ff.probe(out, use_cache=False).require_video()
    assert (v.width, v.height) == (1000, 776)
