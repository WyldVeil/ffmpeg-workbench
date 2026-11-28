"""The Text Blurrer page: finds the text on a dropped video by itself, a click
on a box chooses it (and a second click leaves it), the preview shows the
result, a box can be drawn by hand, and the render blurs what was chosen."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from core import ffmpeg as ff

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def words(tmp_path_factory):
    """A moving picture with a 'handle' (bars for letters) along the bottom."""
    src = str(tmp_path_factory.mktemp("tb") / "words.mp4")
    letters = ",".join(
        f"drawbox=x={40 + i * 22}:y=300:w=12:h=34:color=white:t=fill" for i in range(12)
    )
    subprocess.run(
        [
            ff.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "mandelbrot=s=640x360:r=25:start_scale=1.5:end_scale=0.05", "-t", "6",
            "-vf", f"{letters},format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", src,
        ],
        check=True,
    )  # fmt: skip
    return src


def _ev(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=int(x), y=int(y))


def _click(tab, x: float, y: float) -> None:
    tab._on_press(_ev(x, y))
    tab._on_release(_ev(x, y))


def test_finds_clicks_and_blurs(ui, words, outdir):
    tab = ui.open("Text Blurrer")
    assert tab.load_file(words)
    # (the page may still hold finds from a video an earlier test dropped on it)
    ui.wait_for(lambda: any(it.sure and it.textlike and it.y > 0.7 for it in tab.items), timeout=60)
    ui.wait_for(lambda: tab.preview.image_box() is not None, timeout=30)
    handle = next(it for it in tab.items if it.sure and it.textlike and it.y > 0.7)
    assert not handle.chosen.get()
    x, y, w, h = tab._on_canvas(handle)
    tab._on_hover(_ev(x + w / 2, y + h / 2))
    assert tab._hovered == tab.items.index(handle)
    _click(tab, x + w / 2, y + h / 2)
    assert handle.chosen.get() and len(tab._regions()) == 1
    _click(tab, x + w / 2, y + h / 2)  # again: un-blur
    assert not handle.chosen.get() and tab._regions() == []
    _click(tab, x + w / 2, y + h / 2)
    out = str(outdir / "blurred.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(timeout=180)
    assert os.path.getsize(out) > 0
    assert ff.probe(out, use_cache=False).duration == pytest.approx(6.0, abs=0.2)


def test_a_box_drawn_by_hand_is_chosen(ui, words):
    tab = ui.open("Text Blurrer")
    tab.load_file(words)
    ui.wait_for(lambda: tab.preview.image_box() is not None, timeout=30)
    x0, y0, bw, bh = tab.preview.image_box()
    before = len(tab.items)
    tab._on_press(_ev(x0 + bw * 0.1, y0 + bh * 0.1))
    tab._on_drag(_ev(x0 + bw * 0.3, y0 + bh * 0.25))
    tab._on_release(_ev(x0 + bw * 0.3, y0 + bh * 0.25))
    mine = [it for it in tab.items if it.by_hand]
    assert len(tab.items) == before + 1 and len(mine) == 1 and mine[0].chosen.get()


def test_not_a_video_says_so(ui, media):
    tab = ui.open("Text Blurrer")
    assert not tab.load_file(media["mp3"])  # a drop of audio isn't taken
    tab.src_var.set(media["mp3"])
    ui.wait_for(lambda: "picture" in tab.status.cget("text"), timeout=10)
