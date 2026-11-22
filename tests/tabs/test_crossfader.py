"""Crossfader: command builder verified by real renders, plus the tab end to end."""

import pytest

from core import encoders as enc
from core import ffmpeg as ff
from tabs.cutting import crossfader as cf


def _clips(media, *names):
    return [cf.Clip.from_info(ff.probe(media[n])) for n in names]


def _render(clips, out, T, **kw):
    venc = enc.video("h264", "low", speed="fastest", hw="off", container="mp4")
    cmd, seconds = cf.build_command(clips, out, cf.Crossfade(T, **kw), venc=venc)
    res = ff.run(cmd, duration=seconds)
    assert res.ok, res.error_summary
    return ff.probe(out, use_cache=False), seconds


@pytest.mark.ffmpeg
def test_overlap_mixed_inputs_with_silent_clip(media, outdir):
    clips = _clips(media, "h264", "vertical_noaudio", "hevc10", "vp9")
    T = 1.0
    out = str(outdir / "x.mp4")
    info, seconds = _render(clips, out, T=T, transitions=["fade", "wipeleft", "circleopen"])
    expected = sum(c.duration for c in clips) - 3 * T
    assert seconds == pytest.approx(expected, abs=0.01)
    assert info.duration == pytest.approx(expected, abs=0.15)
    v, a = info.require_video(), info.require_audio()
    assert (v.width, v.height) == (1280, 720) and v.pix_fmt == "yuv420p"
    assert a is not None and a.channels == 2 and a.sample_rate == 48000
    assert abs(v.duration - a.duration) < 0.1  # no A/V drift
    assert abs(v.start_time - a.start_time) < 0.05


@pytest.mark.ffmpeg
def test_freeze_mode_keeps_all_action(media, outdir):
    clips = _clips(media, "h264", "h264_b")
    out = str(outdir / "f.mp4")
    info, seconds = _render(clips, out, T=1.5, transitions=["dissolve"], freeze=True)
    assert seconds == pytest.approx(sum(c.duration for c in clips) + 1.5, abs=0.01)
    assert info.duration == pytest.approx(seconds, abs=0.15)


@pytest.mark.ffmpeg
def test_no_audio_mode_and_all_silent_inputs(media, outdir):
    clips = _clips(media, "vertical_noaudio", "vertical_noaudio")
    info, _ = _render(clips, str(outdir / "s.mp4"), T=0.5, transitions=["fade"])
    assert info.has_video and not info.has_audio
    clips = _clips(media, "h264", "h264_b")
    info, _ = _render(clips, str(outdir / "n.mp4"), T=0.5, transitions=["fade"], audio_mode="none")
    assert not info.has_audio


@pytest.mark.ffmpeg
def test_hostile_filename_and_webm_output(media, outdir):
    clips = _clips(media, "nasty", "h264_b")
    venc = enc.video("vp9", "lowest", speed="fastest", hw="off", container="webm")
    out = str(outdir / "o.webm")
    cmd, seconds = cf.build_command(clips, out, cf.Crossfade(1.0, ["fade"]), venc=venc)
    res = ff.run(cmd, duration=seconds)
    assert res.ok, res.error_summary
    info = ff.probe(out, use_cache=False)
    assert info.require_video().codec == "vp9" and info.require_audio().codec == "opus"


def test_validation_messages(media):
    a = cf.Clip("a", 3.0, 1280, 720, 30, "30/1", True)
    b = cf.Clip("b", 1.5, 1280, 720, 30, "30/1", True)
    c = cf.Clip("c", 3.0, 1280, 720, 30, "30/1", True)
    assert cf.validate([a], 1.0, False)
    assert cf.validate([a, b, c], 1.0, False)  # middle clip needs 2 s
    assert cf.validate([a, b, c], 0.7, False) is None
    assert cf.validate([a, b, c], 1.0, True) is None  # freeze pads the clips
    assert cf.validate([a, c], 0.0, False)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_tab_end_to_end(ui, media, outdir):
    tab = ui.open("Crossfader")
    for row in list(tab.clip_rows):
        row["var"].set("")
        row["clip"] = None
    assert tab.load_file(media["h264"]) and tab.load_file(media["h264_1080p25"])
    ui.pump(0.2)
    out = str(outdir / "tab.mp4")
    tab.out_var.set(out)
    tab.trans_dur_var.set("1.0")
    tab.encoder.set_codec("h264")
    ui.click_text(tab, tab._render_label.strip()[:12])
    ui.wait_idle(180)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    info = ui.assert_media(out, video=True, audio=True, min_duration=9.5, max_duration=10.5)
    assert info["streams"][0]["width"] == 1280
