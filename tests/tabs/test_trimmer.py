"""Quick Trimmer: lossless cuts start A/V together; accurate cuts are frame-exact."""
import pytest

from tabs.cutting import trimmer as tr


@pytest.mark.ffmpeg
def test_keyframe_lookup(media):
    # test clip has a keyframe every 30 frames (1 s)
    assert tr.keyframe_at_or_before(media["h264"], 1.5) == pytest.approx(1.0, abs=0.04)
    assert tr.keyframe_at_or_before(media["h264"], 0.2) == pytest.approx(0.0, abs=0.04)


def test_parse_time_is_strict():
    assert tr.parse_time("01:02.5") == 62.5
    with pytest.raises(ValueError):
        tr.parse_time("1:2:3:4")


def _trim(ui, media, outdir, src, start, end, copy, name):
    tab = ui.open("Quick Trimmer")
    assert tab.load_file(media[src])
    tab._copy_mode.set(copy)
    out = str(outdir / name)
    tab._out_var.set(out)
    tab._start_var.set(start)
    tab._end_var.set(end)
    ui.click_text(tab, tab.btn_render.cget("text")[:6])
    ui.wait_idle(120)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    return tab, out


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_lossless_trim_keeps_av_aligned(ui, media, outdir):
    tab, out = _trim(ui, media, outdir, "h264", "00:01.500", "00:03.500", True, "lossless.mp4")
    info = ui.assert_media(out, video=True, audio=True, min_duration=2.4, max_duration=2.7)
    starts = {s["codec_type"]: float(s.get("start_time", 0)) for s in info["streams"]}
    assert abs(starts["video"] - starts["audio"]) < 0.05, starts     # was ~1 s apart
    assert "keyframe" in ui.console_text(tab).lower()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_accurate_trim_is_exact_and_8bit(ui, media, outdir):
    _tab, out = _trim(ui, media, outdir, "hevc10", "00:01.250", "00:03.250", False, "exact.mp4")
    info = ui.assert_media(out, video=True, audio=True, min_duration=1.95, max_duration=2.08)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v["pix_fmt"] == "yuv420p" and v["codec_name"] == "h264"


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_invalid_time_is_rejected_not_zeroed(ui, media):
    tab = ui.open("Quick Trimmer")
    tab.load_file(media["h264"])
    tab._start_var.set("00:02.000")
    tab._apply_typed_times()
    tab._start_var.set("abc")
    tab._apply_typed_times()
    assert tab._start_var.get() == "00:02.000"
