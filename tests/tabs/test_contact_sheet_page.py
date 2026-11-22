"""The Contact Sheet page: the live preview, stepping through sets, saving."""

from __future__ import annotations

import pytest

from core import ffmpeg as ff
from core.ops import contact_sheet as cs
from tabs.social import contact_sheet as page

pytestmark = [pytest.mark.gui, pytest.mark.ffmpeg]


def _no_errors(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


def _preview_shown(ui, tab):
    """Wait for the newest preview to be on screen; its file."""
    ui.wait_for(
        lambda: tab._after is None and (tab.preview.path or "").endswith(f"sheet{tab._gen}.png"), 90
    )
    return tab.preview.path


def _open(ui, src):
    ui.reset()
    tab = ui.open("Contact Sheet")
    tab.src_row.set(src)
    return tab


def test_the_sheet_appears_and_other_sets_can_be_seen(ui, media):
    tab = _open(ui, media["h264"])
    first = _preview_shown(ui, tab)
    assert "20 frames" in tab.status_lbl.cget("text")
    assert str(tab.btn_prev.cget("state")) == "disabled"

    tab.btn_next.invoke()
    second = _preview_shown(ui, tab)
    assert second != first
    assert tab.set_lbl.cget("text") == "Set 2"
    assert str(tab.btn_prev.cget("state")) == "normal"

    tab.btn_prev.invoke()
    _preview_shown(ui, tab)
    assert tab.set_lbl.cget("text") == "Set 1"

    tab._grid(3, 3)
    _preview_shown(ui, tab)
    assert "9 frames" in tab.status_lbl.cget("text")
    _no_errors(ui)


def test_a_wrong_number_is_explained_not_rendered(ui, media):
    tab = _open(ui, media["h264"])
    _preview_shown(ui, tab)
    tab.cols_var.set("0")
    ui.wait_for(lambda: tab._after is None and "Columns" in tab.status_lbl.cget("text"), 10)
    tab._render()
    assert ui.dialogs.warnings()


@pytest.mark.parametrize(
    ("picture", "sampling"),
    [(page.Picture.PNG, cs.Sampling.EVEN), (page.Picture.JPG, cs.Sampling.SCENES)],
)
def test_saving_makes_the_full_size_sheet(ui, media, outdir, picture, sampling):
    tab = _open(ui, media["h264"])
    tab.sampling.value = sampling
    tab._sampling_changed()
    tab.picture.value = picture
    tab._grid(3, 2)
    tab.numbers_var.set(True)
    _preview_shown(ui, tab)
    out = str(outdir / f"sheet.{picture.value}")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _no_errors(ui)
    layout, labels, picking = tab._settings()
    expected = cs.plan(ff.probe(media["h264"]), layout, labels, picking).geo
    made = ff.probe(out, use_cache=False).require_video()
    assert (made.width, made.height) == (expected.width, expected.height)
