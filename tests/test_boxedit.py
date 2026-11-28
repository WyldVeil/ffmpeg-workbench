"""Dragging boxes on the picture (Manual Crop, Privacy Blur): handles on the
corners and the middle of each edge, an edge grabbed anywhere along it, and
an edge drag that only ever moves that edge - a sideways wobble while
cropping the top must not change the width."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tabs.kit import boxedit

BOX = (100.0, 50.0, 200.0, 100.0)  # x 100-300, y 50-150


@pytest.mark.parametrize(
    ("x", "y", "part"),
    [
        (100, 50, "tl"),
        (300, 50, "tr"),
        (100, 150, "bl"),
        (300, 150, "br"),
        (200, 50, "t"),  # middle handle
        (130, 52, "t"),  # anywhere along the edge
        (270, 148, "b"),
        (101, 100, "l"),
        (299, 120, "r"),
        (200, 100, "move"),
        (50, 100, None),
        (200, 200, None),
    ],
)
def test_hit(x, y, part):
    assert boxedit.hit(BOX, x, y, 8) == part


def test_eight_handles_corners_and_edge_middles():
    pts = boxedit.handle_points(100, 50, 300, 150)
    assert len(pts) == 8
    assert (200, 50) in pts and (200, 150) in pts and (100, 100) in pts and (300, 100) in pts


def test_top_edge_drag_ignores_sideways_movement():
    x, y, w, h = boxedit.drag("t", BOX, dx=37, dy=20, bounds=(640, 360))
    assert (x, w) == (100, 200)
    assert (y, h) == (70, 80)


def test_side_edge_drag_ignores_vertical_movement():
    x, y, w, h = boxedit.drag("r", BOX, dx=40, dy=-25, bounds=(640, 360))
    assert (y, h) == (50, 100)
    assert (x, w) == (100, 240)


def test_corner_drag_moves_both_sides():
    assert boxedit.drag("br", BOX, 10, 20, (640, 360)) == (100, 50, 210, 120)


def test_edges_stay_inside_and_keep_a_minimum_size():
    assert boxedit.drag("t", BOX, 0, -500, (640, 360))[1] == 0
    x, y, w, h = boxedit.drag("l", BOX, 1000, 0, (640, 360), minimum=16)
    assert w == 16 and x == 284


def test_move_stays_inside():
    assert boxedit.drag("move", BOX, 1000, 1000, (640, 360)) == (440, 260, 200, 100)


def test_locked_ratio_edge_drag_keeps_shape_about_the_middle():
    x, y, w, h = boxedit.drag("b", BOX, 0, 50, (640, 360), ratio=(2, 1))
    assert h == 150 and w == 300
    assert x + w / 2 == 200  # same centre
    assert w / h == 2


def test_locked_ratio_corner_drag_keeps_shape():
    x, y, w, h = boxedit.drag("br", BOX, 40, 0, (640, 360), ratio=(2, 1))
    assert (x, y) == (100, 50) and w / h == 2


def _ev(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=int(x), y=int(y))


@pytest.mark.gui
def test_crop_top_middle_handle_crops_only_the_top(ui, media):
    tab = ui.open("Manual Crop")
    tab.load_file(media["h264"])
    ui.wait_for(lambda: tab.vid_w > 0, timeout=20)
    ui.app.update()
    tab.set_rect(200, 100, 600, 400)
    cx, cy, cw, _ch = tab._box_on_canvas()
    tab._press(_ev(cx + cw / 2, cy))
    tab._move(_ev(cx + cw / 2 + 30, cy + 40))  # down, with a sideways wobble
    tab._drag = None
    x, y, w, h = tab.rect()
    assert (x, w) == (200, 600)
    assert y > 100 and y + h == 500


@pytest.mark.gui
def test_privacy_blur_region_can_be_resized_and_moved(ui, media):
    from core.ops.region_blur import Region

    tab = ui.open("Privacy Blur")
    tab.load_file(media["h264"])
    ui.wait_for(lambda: tab.preview.image_box() is not None, timeout=20)
    tab._add(Region(0.25, 0.25, 0.5, 0.5))
    ui.app.update()
    x, y, w, h = tab._on_canvas(tab.regions[0])
    tab._press(_ev(x + w, y + h / 2))  # right edge
    tab._move(_ev(x + w + 20, y + h / 2 + 15))
    tab._release(_ev(0, 0))
    r = tab.regions[0]
    assert r.w > 0.5 and (r.y, r.h) == (0.25, 0.5)
    x, y, w, h = tab._on_canvas(r)
    tab._press(_ev(x + w / 2, y + h / 2))  # inside: move
    tab._move(_ev(x + w / 2 - 10, y + h / 2 - 10))
    tab._release(_ev(0, 0))
    assert tab.regions[0].x < 0.25 and tab.regions[0].y < 0.25
    assert len(tab.regions) == 1  # moving didn't draw a new region
