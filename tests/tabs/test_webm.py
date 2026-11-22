"""WebM Maker end to end: Simple profile and Advanced size cap are honoured."""

import os

import pytest

from core import ffmpeg as ff


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


def _ok(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_simple_profile_fits(ui, media, outdir):
    ui.reset()
    tab = ui.open("WebM Maker")
    tab.src_var.set(media["h264_1080p25"])
    ui.wait_for(lambda: tab._src_dur > 0, 30)
    tab.notebook.select(0)
    tab.simple_profile_var.set("4mb_audio")
    tab._update_simple_estimate()
    assert "kbps video" in tab.simple_est_lbl.cget("text")
    out = str(outdir / "simple.webm")
    tab.out_row.set(out)
    tab._start_render()
    ui.wait_idle(300)
    _ok(ui)
    assert os.path.getsize(out) <= 4 * 1024 * 1024
    info = ff.probe(out, use_cache=False)
    assert info.require_video().codec == "vp9" and info.require_audio().codec == "opus"


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_simple_rejects_too_long(ui, media, outdir):
    ui.reset()
    tab = ui.open("WebM Maker")
    tab.src_var.set(media["h264"])
    ui.wait_for(lambda: tab._src_dur > 0, 30)
    tab.notebook.select(0)
    tab.simple_start_var.set("5.5")  # leaves < 2 s
    tab.out_row.set(str(outdir / "short.webm"))
    tab._start_render()
    ui.pump(0.3)
    assert any("too short" in e[2] for e in ui.dialogs.errors())


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_advanced_tiny_cap_is_enforced(ui, media, outdir):
    ui.reset()
    tab = ui.open("WebM Maker")
    tab.src_var.set(media["h264_1080p25"])
    ui.wait_for(lambda: tab._src_dur > 0, 30)
    tab.notebook.select(1)
    tab.size_limit_var.set("0.3")
    tab.quality_var.set(tab.quality_var.get())
    out = str(outdir / "advanced.webm")
    tab.out_row.set(out)
    tab._start_render()
    ui.wait_idle(300)
    _ok(ui)
    assert os.path.getsize(out) <= 0.3 * 1024 * 1024
