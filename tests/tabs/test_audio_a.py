"""Audio tools, part 1: Voice Changer, Loudness, Extractor, Muter, Replacer, Sync, Silence."""

import pytest

from core import ffmpeg as ff
from core import loudness as ln
from tabs.audio import extractor, muter, silence, sync


def test_pure_helpers():
    zones = silence.keep_zones([(1.0, 2.0), (5.0, 9.0)], 10.0, 0.1, fps=25)
    expected = [(0.0, 1.1), (1.9, 5.1), (8.9, 10.0)]          # padding 0.1, snapped to 1/25 s
    assert all(abs(a - x) <= 0.04 and abs(b - y) <= 0.04 for (a, b), (x, y) in zip(zones, expected, strict=False))
    assert all(round(v * 25, 6) == round(v * 25) for z in zones for v in z)
    g = silence.cut_graph(zones, True, True)
    assert "split=3" in g and "concat=n=3:v=1:a=1[v][a]" in g
    assert muter.gain_expr([], 0.1) == "1"
    assert sync.reencode_filter(-0.25) == "atrim=start=0.250,asetpts=PTS-STARTPTS"
    assert extractor.copy_extension("pcm_s24le") == ".wav" and extractor.copy_extension("aac") == ".m4a"
    m = ln.parse_measurement(['[Parsed_loudnorm_0 @ 0x1]', '{', '"input_i" : "-20.5",', '"input_tp" : "-3.0",',
                              '"input_lra" : "5.0",', '"input_thresh" : "-31.0",', '"target_offset" : "0.2"', '}'])
    assert m.input_i == -20.5 and "measured_I=-20.50" in ln.normalise_filter(-16, -1.5, 11, m, 44100)


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
@pytest.mark.parametrize("ext", [".mp3", ".mp4"])
def test_voice_changer(ui, media, outdir, ext):
    ui.reset()
    tab = ui.open("Voice Changer")
    _src(ui, tab, media["h264"])
    out = str(outdir / f"voice{ext}")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_audio and info.has_video == (ext == ".mp4")
    assert info.duration == pytest.approx(ff.duration_of(media["h264"]), abs=0.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_loudness_two_pass(ui, media, outdir):
    ui.reset()
    tab = ui.open("Loudness Normalizer")
    _src(ui, tab, media["wav_silence"])
    out = str(outdir / "loud.wav")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    m = ln.measure(out, -16, -1.5, 11)
    assert m.input_i == pytest.approx(-16, abs=1.0)
    assert ff.probe(out, use_cache=False).audio.sample_rate == ff.probe(media["wav_silence"]).audio.sample_rate


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("fmt", ["copy", "mp3", "flac"])
def test_extractor(ui, media, outdir, fmt):
    ui.reset()
    tab = ui.open("Audio Extractor")
    _src(ui, tab, media["hevc10"])
    tab.fmt_var.set(extractor.FORMATS[fmt][0])
    tab._suggest_name(force=True)
    out = str(outdir / ("track" + tab._ext()))
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_audio and not info.has_video
    assert info.require_audio().codec == {"copy": "opus", "mp3": "mp3", "flac": "flac"}[fmt]


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("mode", ["bleep", "remove"])
def test_muter(ui, media, outdir, mode):
    ui.reset()
    tab = ui.open("The Muter")
    _src(ui, tab, media["h264"])
    tab.ranges.clear()
    tab.add_range(1.0, 2.0)
    tab.mode_var.set(mode)
    out = str(outdir / f"muted_{mode}.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.has_audio == (mode != "remove")


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_replacer_keeps_video_length(ui, media, outdir):
    ui.reset()
    tab = ui.open("Audio Replacer")
    _src(ui, tab, media["h264"])
    tab.audio_row.set(media["mp3"])
    out = str(outdir / "replaced.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    assert info.require_video().duration == pytest.approx(ff.duration_of(media["h264"]), abs=0.1)
    assert info.require_audio().duration == pytest.approx(info.require_video().duration, abs=0.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_silence_remover(ui, media, outdir):
    ui.reset()
    tab = ui.open("Silence Remover")
    _src(ui, tab, media["wav_silence"])
    out = str(outdir / "tight.wav")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    _ok(ui)
    assert ff.duration_of(out) < ff.duration_of(media["wav_silence"]) - 0.5
