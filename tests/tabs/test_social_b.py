"""Animated Titles, Intro/Outro Maker, Frame Extractor, Hard-Subber and
Slideshow Maker: pure helpers plus real renders through the GUI."""

import os

import pytest

from core import ass
from core import ffmpeg as ff
from tabs.social import frame_extractor, intro_maker, slideshow, titles

NASTY_TEXT = "Don't stop: 50% off, {now} [ok]; \\o/ \\N é"


def test_ass_escape_keeps_text_literal():
    esc = ass.escape(NASTY_TEXT)
    assert "\\{now\\}" in esc and "{" not in esc.replace("\\{", "")
    assert "\\" + ass.ZWSP + "N" in esc  # a literal backslash-N, not a line break
    assert ass.escape("a\nb") == "a\\Nb"
    assert ass.colour("#112233", 0.0) == "&H00332211"
    assert ass.ts(3725.456) == "1:02:05.46"


def test_title_script_all_styles_scale():
    cards = [
        titles.Card(style, NASTY_TEXT, "second line", i * 3.0, 2.5)
        for i, style in enumerate(titles.STYLES)
    ]
    for w, h in ((1280, 720), (1080, 1920), (3840, 2160)):
        text = titles.build_script(cards, w, h).render()
        assert f"PlayResX: {w}" in text and text.count("Dialogue:") >= len(cards) * 2


def test_frame_helpers():
    assert frame_extractor.keep_scenes([0.5, 3.0, 3.5, 9.0], 2.0) == [0.0, 3.0, 9.0]
    assert frame_extractor.quality_args("jpg", 100)[-1] == "2"
    assert frame_extractor.quality_args("jpg", 1)[-1] == "31"
    starts, total = slideshow.timeline(3, 4.0, 1.0)
    assert starts == [0.0, 3.0, 6.0] and total == 10.0


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


def _no_errors(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


def _src(ui, tab, path):
    tab.src_var.set(path)
    ui.pump(0.6)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("source", ["h264", "vertical_noaudio"])
def test_titles_render(ui, media, outdir, source):
    ui.reset()
    tab = ui.open("Animated Titles")
    _src(ui, tab, media[source])
    for r in list(tab.rows):
        tab._remove(r)
    for i, style in enumerate(titles.STYLES):
        row = tab.add_card(titles.Card(style, NASTY_TEXT, "line two", i * 0.6, 1.0))
        assert row in tab.rows
    out = str(outdir / f"titles_{source}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    ui.assert_media(out, video=True, min_duration=1.5)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "mode,bg,anim",
    [("none", "gradient", "typewriter"), ("both", "file", "slide"), ("before", "solid", "zoom")],
)
def test_intro_maker(ui, media, outdir, mode, bg, anim):
    ui.reset()
    tab = ui.open("Intro/Outro Maker")
    tab.title_var.set(NASTY_TEXT)
    tab.sub_var.set("Subscribe")
    tab.bg_var.set(intro_maker.BACKGROUNDS[bg])
    tab._bg_changed()
    if bg == "file":
        tab.bg_row.set(media["png"])
        tab.music_row.set(media["mp3"])
    tab.anim_var.set(intro_maker.ANIMATIONS[anim])
    tab.dur_var.set("2")
    tab.attach_var.set(intro_maker.ATTACH[mode])
    if mode != "none":
        tab.main_row.set(media["hevc10"])
    out = str(outdir / f"intro_{mode}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(300)
    _no_errors(ui)
    main = ff.duration_of(media["hevc10"])
    expected = {"none": 2.0, "both": main + 4.0, "before": main + 2.0}[mode]
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(expected, abs=0.25)
    assert info.has_audio and info.has_video
    if mode != "none":
        assert (info.width, info.height) == (1280, 720)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "mode,fmt", [("single", "jpg"), ("interval", "png"), ("scenes", "webp"), ("sheet", "jpg")]
)
def test_frame_extractor(ui, media, outdir, mode, fmt):
    ui.reset()
    tab = ui.open("Frame Extractor")
    _src(ui, tab, media["nasty"])
    folder = outdir / f"frames_{mode}"
    tab.dir_var.set(str(folder))
    tab.mode_var.set(mode)
    tab._mode_changed()
    tab.fmt_var.set(frame_extractor.FORMATS[fmt])
    if mode == "single":
        tab.time_var.set("1.5")
    if mode == "interval":
        tab.every_var.set("1")
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    files = [os.path.join(dp, f) for dp, _d, fs in os.walk(folder) for f in fs]
    assert files, "nothing extracted"
    assert all(f.endswith("." + fmt) for f in files)
    if mode == "interval":  # one frame per second of the clip
        assert abs(len(files) - round(ff.duration_of(media["nasty"]))) <= 1
    if mode == "scenes":
        assert any("scene_0001" in f for f in files)  # the first scene is always kept
    if mode == "sheet":
        assert len(files) == 1 and os.path.getsize(files[0]) > 10000


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_hard_subber_legacy_encoding_and_odd_name(ui, media, outdir, tmp_path):
    ui.reset()
    tab = ui.open("Hard-Subber")
    _src(ui, tab, media["h264"])
    odd = tmp_path / "it's, [odd]; name.srt"
    odd.write_bytes(
        "1\n00:00:00,500 --> 00:00:03,000\nCafé crème — 50% {x}\n".encode("cp1252", "replace")
    )
    tab.sub_row.set(str(odd))
    tab.box_var.set(True)
    ui.pump(0.6)
    out = str(outdir / "subbed.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    ui.assert_media(out, video=True, audio=True, min_duration=3.5)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("trans", ["fade", "cut"])
def test_slideshow(ui, media, outdir, tmp_path, trans):
    ui.reset()
    tab = ui.open("Slideshow Maker")
    tab._clear()
    portrait = tmp_path / "portrait.jpg"
    ff.check(
        [
            ff.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=600x900:d=1",
            "-frames:v",
            "1",
            str(portrait),
        ]
    )
    added = tab.add_paths([media["png"], str(portrait), media["png"]])
    assert added == 3
    tab.slides[0].caption = NASTY_TEXT
    tab.dur_var.set("2")
    tab.trans_dur_var.set("0.5")
    tab.trans_var.set(slideshow.TRANSITIONS[trans])
    tab.music_row.set(media["mp3"])
    out = str(outdir / f"slides_{trans}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(300)
    _no_errors(ui)
    info = ff.probe(out, use_cache=False)
    expected = 6.0 - (1.0 if trans == "fade" else 0.0)
    assert info.duration == pytest.approx(expected, abs=0.2)
    assert (info.width, info.height) == (1920, 1080) and info.has_audio
