"""The All-in-One Builder: tools as steps, in their own windows, rendered together."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import pytest

from core import ffmpeg as ff

pytestmark = [pytest.mark.gui, pytest.mark.ffmpeg]


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness

    return Harness.instance()


def _builder(ui, media) -> Any:
    ui.reset()
    tab = ui.open("All-in-One Builder")
    for s in list(tab.steps):
        tab._remove(s)
    tab.load_file(media["h264"])
    ui.pump(0.3)
    return tab


def _open_host(ui, tab, kind: str) -> Any:
    from tabs.builder.step_mode import HOSTS, StepHost

    tab._add(kind)

    def host() -> StepHost | None:
        return next((h for h in HOSTS.values() if h.win.winfo_ismapped()), None)

    ui.wait_for(lambda: host() is not None, 240)
    return host()


def add_step(ui, tab, kind: str, setup: Callable[[Any], object] | None = None) -> Any:
    """Add a *kind* step: its window opens; *setup(tool page)* sets it; Save."""
    n = len(tab.steps)
    host = _open_host(ui, tab, kind)
    if setup is not None:
        setup(host.tab)
        ui.pump(0.3)
    host._save_pressed()
    ui.wait_for(lambda: len(tab.steps) > n or bool(ui.dialogs.warnings()), 60)
    assert len(tab.steps) > n, ui.dialogs.log
    assert not host.win.winfo_ismapped()
    return tab.steps[n]


def _crop(page: Any) -> None:
    page.set_rect(0, 0, 640, 360)


def _blur_a_box(page: Any) -> None:
    from tabs.visuals.text_blur import Item

    it = Item(0.1, 0.1, 0.3, 0.2, 0.0, None, textlike=True, sure=True, by_hand=True)
    it.chosen.set(True)
    page.items.append(it)
    page.cover_var.set(True)
    page.cover_kind.set("text")
    page.cover_text_var.set("HELLO")
    page._cover_toggled()


def test_steps_render_in_one_go(ui, media, outdir):
    tab = _builder(ui, media)
    crop = add_step(ui, tab, "crop", _crop)
    assert crop.cmds and "crop=640:360:0:0" in " ".join(crop.cmds[0])
    blur = add_step(ui, tab, "text_blur", _blur_a_box)  # works on the cropped video
    assert blur.in_path != media["h264"] and os.path.exists(blur.in_path)
    assert ff.probe(blur.in_path, use_cache=False).width == 640
    assert "drawtext" in " ".join(blur.cmds[0])
    add_step(ui, tab, "denoise")
    assert tab.plan_lbl.cget("text").startswith("✔")
    passes = tab._passes(str(outdir / "x.mp4"))
    assert passes is not None and len(passes) == 1  # all three joined
    out = str(outdir / "built.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(300)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    info = ff.probe(out, use_cache=False)
    assert (info.width, info.height) == (640, 360)
    assert info.duration == pytest.approx(6, abs=0.3) and info.has_audio


def test_python_step_moved_is_set_up_again(ui, media, outdir):
    tab = _builder(ui, media)
    add_step(ui, tab, "sharpen")
    speed = add_step(ui, tab, "speed_ramp")
    assert speed.run is not None and speed.in_path.startswith(tab.workdir)
    tab._move(speed, -1)  # now first: it must read the video itself
    assert tab._moved_python(0, tab.steps[0])
    out = str(outdir / "moved.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_for(lambda: not tab._busy, 120)  # set up again first, then queued
    ui.wait_idle(300)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert os.path.abspath(tab.steps[0].in_path) == os.path.abspath(media["h264"])
    assert ff.probe(out, use_cache=False).has_video


def test_earlier_change_flags_placed_steps(ui, media):
    tab = _builder(ui, media)
    add_step(ui, tab, "denoise")
    crop = add_step(ui, tab, "crop", _crop)
    assert not crop.check
    first = tab.steps[0]
    tab._move(first, 1)  # crop is first now: it reads something else
    assert crop.check
    tab._settings(crop)  # opening and saving clears it
    host = crop.host
    ui.wait_for(lambda: host.win.winfo_ismapped(), 120)
    host._save_pressed()
    ui.wait_for(lambda: not host.win.winfo_ismapped(), 60)
    assert not crop.check


def test_discard_keeps_the_settings(ui, media):
    tab = _builder(ui, media)
    step = add_step(ui, tab, "crop", _crop)
    tab._settings(step)
    host = step.host
    ui.wait_for(lambda: host.win.winfo_ismapped(), 120)
    host.tab.set_rect(10, 10, 320, 180)
    host._close_asked()  # the harness answers "yes, discard"
    assert host.tab.rect() == (0, 0, 640, 360)
    assert not tab._busy


def test_recipe_round_trip(ui, media, tmp_path):
    from tabs.all_in_one import recipe_data, steps_from_recipe

    tab = _builder(ui, media)
    add_step(ui, tab, "crop", _crop)
    add_step(ui, tab, "speed_ramp")
    data = json.loads(json.dumps(recipe_data(tab.steps, tab.src, "h264")))
    again = steps_from_recipe(data, tab.src)
    assert [s.kind for s in again] == ["crop", "speed_ramp"]
    assert not again[0].stale and again[1].stale  # Python jobs are set up again
    other = steps_from_recipe(data, media["h264_b"])
    assert all(s.stale for s in other)
    with pytest.raises(ValueError):
        steps_from_recipe({"version": 1, "steps": [{"kind": "nonsense"}]}, tab.src)


def test_last_step_decides_the_format(ui, media):
    tab = _builder(ui, media)
    add_step(ui, tab, "denoise")
    add_step(ui, tab, "gif_maker")
    assert tab._out_ext() == ".gif"
    assert not tab.encoder.winfo_manager() and tab.format_note.winfo_manager()
