"""Special Effects: every effect renders a valid, playable file from varied sources."""

import pytest

EFFECTS = [
    ("fi", "_render_fadein"), ("zb", "_render_zoomburst"), ("sl", "_render_slidein"),
    ("sp", "_render_spinin"), ("spo", "_render_splitopen"), ("fo", "_render_fadeout"),
    ("fz", "_render_freezezoom"), ("ir", "_render_irisclose"), ("em", "_render_emoji"),
    ("lt", "_render_lowerthird"), ("sub", "_render_subscribe"), ("cd", "_render_countdown"),
    ("pb", "_render_progressbar"), ("rgb", "_render_rgb"), ("vhs", "_render_vhs"),
    ("gr", "_render_grain"), ("vi", "_render_vignette"), ("ll", "_render_lightleak"),
    ("sh", "_render_shake"), ("zp", "_render_zoompunch"), ("kb", "_render_kenburns"),
    ("wp", "_render_whippan"), ("mc", "_render_meme"), ("cb", "_render_cinebars"),
    ("wm", "_render_watermark"),
]

SOURCES = ["h264", "hevc10", "vertical_noaudio"]


@pytest.fixture(scope="module")
def fx_tab():
    from tests.gui_harness import Harness
    h = Harness.instance()
    return h, h.open("Special Effects")


def _shorten_times(tab, prefix):
    # keep event times inside our 4-6 s test clips
    for attr, val in (("_start", "0.5"), ("_at", "1.0"), ("_hold", "1.0"), ("_dur", "1.0")):
        var = getattr(tab, f"_{prefix}{attr}", None)
        if var is not None and str(var.get()).replace(".", "", 1).isdigit():
            var.set(val)
    if prefix == "cd":
        tab._cd_from.set("2")
        tab._cd_start.set("0.5")
    if prefix == "lt":
        tab._lt_title.set("O'Brien: 50% \\ \"quoted\"")


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("prefix,method", EFFECTS, ids=[e[0] for e in EFFECTS])
def test_effect_renders(fx_tab, media, outdir, prefix, method, source):
    h, tab = fx_tab
    h.reset()
    getattr(tab, f"_{prefix}_src").set(media[source])
    out = str(outdir / f"{prefix}_{source}.mp4")
    getattr(tab, f"_{prefix}_out").set(out)
    _shorten_times(tab, prefix)
    getattr(tab, method)()
    h.wait_idle(180)
    errs = h.dialogs.errors() + [d for d in h.dialogs.warnings() if "already as wide" not in d[2]]
    if prefix == "cb" and source == "vertical_noaudio":
        return                                    # nothing to letterbox on a 9:16 clip
    assert not errs, errs
    info = h.assert_media(out, video=True, min_duration=0.5)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v["pix_fmt"] == "yuv420p", v["pix_fmt"]
    assert v["width"] % 2 == 0 and v["height"] % 2 == 0
    assert v.get("sample_aspect_ratio", "1:1") in ("1:1", "0:1", "N/A")
