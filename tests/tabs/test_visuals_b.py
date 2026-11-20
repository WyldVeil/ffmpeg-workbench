"""Colour Correct, Colour Match, LUT, Scopes, Deshaker, Green Screen, Speed
Ramper and Media Generator: pure helpers plus real renders through the GUI."""
import os
import shutil

import pytest

from core import ffmpeg as ff
from tabs.visuals import color_correct, green_key, lut, media_gen, scopes, speed_ramp


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_colour_chain_neutral_and_curves():
    assert color_correct.build_chain(color_correct.DEFAULTS) == ""
    chain = color_correct.build_chain({"blacks": -0.1, "whites": 0.1, "shadows": 0.5})
    assert "curves=all='0.100/0 0.25/0.310 0.75/0.750 0.900/1':interp=pchip" in chain
    for look in color_correct.LOOKS.values():
        values = dict(color_correct.DEFAULTS, **look)
        color_correct.build_chain(values)


def test_speed_curve_maths():
    pts = speed_ramp.normalise_points([(2, 1.0), (4, 3.0)], 10)
    assert pts[0] == (0.0, 1.0) and pts[-1] == (10, 3.0)
    # 0-2 s at 1x, 2-4 s ramp 1->3x (ln 3 / 1 s), 4-10 s at 3x
    expected = 2 + __import__("math").log(3) + 2
    assert speed_ramp.output_duration(pts) == pytest.approx(expected, abs=1e-6)
    assert speed_ramp.atempo_chain(0.2) == "atempo=0.5,atempo=0.5,atempo=0.800000"
    assert "rubberband tempo" in speed_ramp.tempo_commands(pts, "rubberband")
    assert speed_ramp.tempo_commands(pts, "atempo").count("atempo@") >= 4


def test_lut_kinds(tmp_path, media):
    one_d = tmp_path / "c.cube"
    one_d.write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")
    assert lut.lut_kind(str(one_d)) == "1d"
    assert lut.lut_kind(media["cube"]) == "3d"
    assert lut.lut_kind(str(tmp_path / "h.png")) == "hald"
    assert "blend=all_mode=normal:all_opacity=0.500" in lut.build_chain(media["cube"], intensity=0.5)


def test_scope_layout_sizes():
    assert scopes.scope_box("vectorscope", 1280, 720) == (720, 720)
    assert scopes.scope_box("waveform", 1080, 1920, fit="width") == (1080, 608)


def test_green_key_chains():
    assert green_key.spill_type("00B140") == "green" and green_key.spill_type("0047BB") == "blue"
    assert "despill=type=green" in green_key.key_chain("chroma", "00B140", 0.1, 0.1, True)
    assert green_key.key_chain("color", "FF0000", 0.1, 0.1, True).startswith("colorkey")
    assert "lumakey=threshold=1" in green_key.key_chain("luma_white", "FFFFFF", 0.1, 0.1, True)


# ── renders through the GUI ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness
    return Harness.instance()


def _video(info):
    return next(s for s in info["streams"] if s["codec_type"] == "video")


def _no_errors(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


def _set_source(ui, tab, path):
    tab.src_var.set(path)
    ui.pump(0.6)              # FileRow on_change is debounced


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_colour_correct_render(ui, media, outdir):
    ui.reset()
    tab = ui.open("Basic Color Corrector")
    _set_source(ui, tab, media["hevc10"])
    tab._apply_look("Punchy")
    out = str(outdir / "graded.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    info = ui.assert_media(out, video=True, audio=True, min_duration=3.5)
    assert _video(info)["pix_fmt"] in ("yuv420p", "yuv420p10le")


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_colour_match_measures_and_renders(ui, media, outdir):
    ui.reset()
    tab = ui.open("Colour Match")
    _set_source(ui, tab, media["h264"])
    tab.ref_row.set(media["hevc10"])
    ui.pump(0.6)
    tab._analyse()
    ui.wait_for(lambda: tab._current_stats() is not None, 120)
    src_stats, ref_stats = tab._current_stats()
    assert src_stats != ref_stats
    assert tab.current_filter().startswith("lutrgb=")
    out = str(outdir / "matched.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    ui.assert_media(out, video=True, min_duration=3.5)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("which", ["builtin", "weird_path", "hald"])
def test_lut_render(ui, media, outdir, tmp_path, which):
    ui.reset()
    tab = ui.open("LUT Applicator")
    _set_source(ui, tab, media["nasty"])
    if which == "builtin":
        tab.look_var.set("Teal & orange")
        tab.intensity_var.set(0.6)
    else:
        folder = tmp_path / "it's, a [dir]"
        folder.mkdir()
        if which == "weird_path":
            path = folder / "wa'rm, x.cube"
            shutil.copyfile(media["cube"], path)
        else:
            path = folder / "hald.png"
            ff.check([ff.ffmpeg_path(), "-y", "-f", "lavfi", "-i", "haldclutsrc=8", "-vf", "hue=h=40",
                      "-frames:v", "1", str(path)])
        tab.look_var.set(lut._CUSTOM)
        tab.lut_row.set(str(path))
    out = str(outdir / f"lut_{which}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    ui.assert_media(out, video=True, audio=True, min_duration=3.5)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("layout", ["side", "grid"])
def test_scope_video(ui, media, outdir, layout):
    ui.reset()
    tab = ui.open("Video Scopes")
    _set_source(ui, tab, media["vertical_noaudio"])
    tab.scope_var.set("vectorscope")
    tab.layout_var.set(scopes.LAYOUTS[layout])
    out = str(outdir / f"scope_{layout}.mp4")
    tab.out_row.set(out)
    tab._render_video()
    ui.wait_idle(240)
    _no_errors(ui)
    v = _video(ui.assert_media(out, video=True, audio=False, min_duration=1.5))
    assert v["width"] % 2 == 0 and v["height"] % 2 == 0


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_deshaker_two_pass_with_comparison(ui, media, outdir):
    ui.reset()
    tab = ui.open("Deshaker")
    _set_source(ui, tab, media["nasty"])
    tab.compare_var.set(True)
    out = str(outdir / "stable.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(300)
    _no_errors(ui)
    v = _video(ui.assert_media(out, video=True, audio=True, min_duration=3.5))
    assert v["width"] == 2560          # original | stabilised
    assert not [f for f in os.listdir(outdir) if ".part" in f]


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("bg", ["image", "colour", "alpha_mov", "alpha_webm"])
def test_green_key(ui, media, outdir, bg):
    ui.reset()
    tab = ui.open("Green Screen Keyer")
    _set_source(ui, tab, media["greenscreen"])
    # auto-sampled from the edges: the plate's green (exact value depends on the FFmpeg that made it)
    plate = green_key.sample_border_colour(media["greenscreen"], tab._info.duration * 0.1)
    assert plate and green_key.spill_type(plate) == "green" and plate != "00B140"   # not the start colour
    ui.wait_for(lambda: tab.key_colour == plate, 30)
    ext = ".mp4"
    if bg == "image":
        tab.bg_mode.set(green_key.BACKGROUNDS["file"])
        tab.bg_row.set(media["png"])
    elif bg == "colour":
        tab.bg_mode.set(green_key.BACKGROUNDS["color"])
    else:
        tab.bg_mode.set(green_key.BACKGROUNDS["alpha"])
        ext = ".mov" if bg == "alpha_mov" else ".webm"
    tab._bg_mode_changed()
    out = str(outdir / f"keyed_{bg}{ext}")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    info = ui.assert_media(out, video=True, min_duration=3.5, max_duration=4.6)
    v = _video(info)
    if bg == "alpha_mov":
        assert v["pix_fmt"].startswith("yuva")
    elif bg == "alpha_webm":
        assert v.get("tags", {}).get("alpha_mode") == "1"


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("mode", ["double", "half_pitch", "ramp"])
def test_speed(ui, media, outdir, mode):
    ui.reset()
    tab = ui.open("Speed Ramper")
    _set_source(ui, tab, media["h264"])
    src_dur = ff.duration_of(media["h264"])
    if mode == "ramp":
        tab.mode_var.set("ramp")
        tab._on_mode()
        tab.preset_var.set("Speed up in the middle")
        tab._apply_preset()
        ui.pump(0.3)
        expected = speed_ramp.output_duration(speed_ramp.normalise_points(tab.parsed_points(), src_dur))
    else:
        tab.mode_var.set("constant")
        tab._on_mode()
        speed = 2.0 if mode == "double" else 0.5
        tab.speed_var.set(speed)
        if mode == "half_pitch":
            tab.audio_var.set(speed_ramp.AUDIO["pitch"])
        expected = src_dur / speed
    out = str(outdir / f"speed_{mode}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    info = ff.probe(out, use_cache=False)
    assert info.video.duration == pytest.approx(expected, abs=0.12)
    assert info.audio.duration == pytest.approx(expected, abs=0.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("pattern,audio,ext", [("smpte_hd", "tone18", ".mp4"),
                                              ("countdown", "pop", ".mov"),
                                              ("none", "pink", ".wav")])
def test_media_generator(ui, outdir, pattern, audio, ext):
    ui.reset()
    tab = ui.open("Media Generator")
    tab.pattern_var.set(media_gen.PATTERNS[pattern])
    tab._pattern_changed()
    tab.audio_var.set(media_gen.AUDIO[audio])
    tab.size_var.set("1280×720 (HD)")
    tab.dur_var.set("4")
    out = str(outdir / f"gen_{pattern}{ext}")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    info = ui.assert_media(out, video=pattern != "none", audio=True, min_duration=3.9,
                           max_duration=4.1)
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert int(a["channels"]) == 2
