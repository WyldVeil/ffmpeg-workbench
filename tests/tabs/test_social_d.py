"""Chapter Markers, Thumbnail Maker and Video Collage."""
import os

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
    tab._slots.clear()
    for key in ("h264", "vertical_noaudio", "hevc10", "vp9"):
        tab._slots.append({"path": media[key], "name": os.path.basename(media[key])})
    tab._layout_var.set(layout)
    tab._duration_mode.set(mode)
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
