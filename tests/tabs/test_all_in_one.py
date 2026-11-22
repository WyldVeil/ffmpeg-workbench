"""All-in-One Builder: combined sections produce one correct command."""

import pytest

from core import ffmpeg as ff
from core.ops import all_in_one as aio


def test_codec_args_per_family():
    nv = aio.video_codec_args("h264_nvenc", "20", "slow", "mp4")
    assert "p6" in nv and "-cq" in nv and "slow" not in nv
    x = aio.video_codec_args("libx265", "22", "medium", "mp4")
    assert x[-2:] == ["-tag:v", "hvc1"]
    assert aio.codec_container("prores_ks", "/a/b.mp4") == "/a/b.mov"


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_pipeline_trim_speed_text_fade(ui, media, outdir):
    ui.reset()
    tab = ui.open("All-in-One Builder")
    out = str(outdir / "pipeline.mp4")
    tab._out_var.set(out)
    tab._src_var.set(media["hevc10"])
    ui.wait_for(lambda: getattr(tab, "_duration", 0) > 0, 30)
    tab._sec_trim.enabled.set(True)
    tab._trim_range.set(1, 4)
    tab._sec_speed.enabled.set(True)
    tab._speed_val.set("1.5")
    tab._sec_text.enabled.set(True)
    tab._text_val.set("It's 50% off: \\o/ {now}")
    tab._sec_fade.enabled.set(True)
    tab._sec_scale.enabled.set(True)
    tab._scale_w.set("854")
    tab._scale_h.set("-1")
    tab._rebuild_cmd()
    cmd = tab._get_cmd_from_text()
    assert "-to" in cmd and cmd.index("-to") < cmd.index("-i")
    tab._render()
    ui.wait_idle(240)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(3 / 1.5, abs=0.1)
    assert info.width == 854 and info.height % 2 == 0
    assert info.require_video().pix_fmt == "yuv420p"
