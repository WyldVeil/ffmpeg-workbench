"""The Text Blurrer's finder: text laid over a video is found with its box and
the times it's there; the picture's own edges, borders and still scenery are
not offered as sure finds."""

from __future__ import annotations

import os
import subprocess

import pytest

from core import ffmpeg as ff
from core.ops import text_find as tf

W, H = 1280, 720  # the video the grids pretend to come from
GW, GH = tf.grid_size(W, H)
DUR = 15.0
RATE = tf.rate_for(DUR)
N = round(DUR * RATE)


def _grids(
    marks: list[tuple[int, int, int, int, range]], motion: float = 10.0, value: int = 200
) -> list[tf.Grid]:
    """N samples; each mark (x, y, w, h cells, samples) is strong where it is."""
    out = []
    for i in range(N):
        cells = bytearray(GW * GH)
        for x, y, w, h, when in marks:
            if i in when:
                for r in range(y, y + h):
                    for c in range(x, x + w):
                        cells[r * GW + c] = value
        out.append(tf.Grid(i / RATE, bytes(cells), motion))
    return out


def test_text_for_the_whole_video_is_found_and_sure():
    found = tf.find(_grids([(10, 70, 30, 5, range(N))]), W, H, DUR)
    assert len(found) == 1
    f = found[0]
    assert (f.start, f.end, f.textlike, f.sure) == (0.0, None, True, True)
    assert f.x == pytest.approx(10 / GW) and f.w == pytest.approx(30 / GW)
    assert f.y == pytest.approx(70 / GH) and f.h == pytest.approx(5 / GH)


def test_text_for_a_while_has_its_times():
    a, b = round(4 * RATE), round(10 * RATE)
    found = tf.find(_grids([(100, 10, 40, 5, range(a, b))]), W, H, DUR)
    assert len(found) == 1
    f = found[0]
    # the grids average the stretch before each sample: times come out about right
    assert 2.0 <= f.start <= 4.5
    assert f.end is not None and 8.0 <= f.end <= 11.0


def test_short_flashes_are_not_offered():
    found = tf.find(_grids([(100, 10, 40, 5, range(8, 10))]), W, H, DUR)
    assert found == []


def test_lines_and_specks_are_not_text():
    marks = [
        (0, 40, GW, 1, range(N)),  # a border line across the frame
        (5, 5, 2, 2, range(N)),  # a speck
    ]
    assert tf.find(_grids(marks), W, H, DUR) == []


def test_two_stacked_edges_are_only_a_maybe():
    marks = [(20, 30, 40, 1, range(N)), (20, 33, 40, 1, range(N))]  # rows 30 and 33, gap between
    found = tf.find(_grids(marks), W, H, DUR)
    assert found and not any(f.sure for f in found)


def test_still_camera_keeps_only_lines_of_text():
    marks = [
        (20, 20, 12, 12, range(N)),  # a square shape in the scenery
        (60, 70, 40, 4, range(N)),  # a line of text
    ]
    found = tf.find(_grids(marks, motion=1.0), W, H, DUR)
    assert len(found) == 1 and found[0].x == pytest.approx(60 / GW)


def test_a_logo_for_most_of_the_video_is_offered_but_not_as_text():
    found = tf.find(_grids([(140, 4, 10, 10, range(N))]), W, H, DUR)
    assert len(found) == 1 and not found[0].textlike and found[0].sure


def test_padding_stays_inside_the_frame():
    f = tf.Found(0.0, 0.95, 0.3, 0.05, 0.0, None).padded()
    assert f.x == 0.0 and f.y + f.h <= 1.0 and f.w > 0.3


def test_nothing_found_in_nothing():
    assert tf.find([], W, H, DUR) == []


def test_finds_overlays_on_a_moving_picture(tmp_path):
    """A zooming fractal (always moving) with a 'handle' for the whole video and a
    'caption' from 4 s to 8 s - letters drawn as bars, so no font is needed."""
    src = str(tmp_path / "overlay.mp4")
    letters = ",".join(
        f"drawbox=x={40 + i * 22}:y=300:w=12:h=34:color=white:t=fill" for i in range(12)
    )
    caption = ",".join(
        f"drawbox=x={360 + i * 20}:y=30:w=10:h=30:color=yellow:t=fill:enable='between(t,4,8)'"
        for i in range(10)
    )
    cmd = [
        ff.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "mandelbrot=s=640x360:r=25:start_scale=1.5:end_scale=0.05",
        "-t", "12", "-vf", f"{letters},{caption},format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", src,
    ]  # fmt: skip
    if subprocess.run(cmd, check=False).returncode or not os.path.exists(src):
        pytest.skip("this FFmpeg can't make the test video")
    # text-shaped finds only: the fractal's zoom centre never changes either, and is offered as a logo
    found = [
        f for f in tf.find(tf.sample(src, 640, 360, 12.0), 640, 360, 12.0) if f.sure and f.textlike
    ]
    handle = [f for f in found if f.y * 360 > 250]
    caption_ = [f for f in found if f.y * 360 < 80]
    assert len(handle) == 1 and handle[0].start == 0.0 and handle[0].end is None
    assert handle[0].x * 640 < 50 and (handle[0].x + handle[0].w) * 640 > 280
    assert len(caption_) == 1 and caption_[0].end is not None
    assert 2.5 <= caption_[0].start <= 4.5 and 7.5 <= caption_[0].end <= 9.5
