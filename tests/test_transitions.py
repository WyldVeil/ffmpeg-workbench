"""core/ops/transitions.py: every transition offered really renders."""

from __future__ import annotations

import pytest

from core import ffmpeg as ff
from core import proc
from core.ops import transitions as tr

pytestmark = pytest.mark.ffmpeg


def _two_clips() -> list[str]:
    a = "testsrc2=s=160x90:r=10:d=1"
    b = "smptehdbars=s=160x90:r=10:d=1"
    return ["-f", "lavfi", "-i", a, "-f", "lavfi", "-i", b]


@pytest.mark.parametrize("name", list(tr.LABELS))
def test_each_transition_renders(name, tmp_path):
    out = str(tmp_path / "t.mp4")
    graph = f"[0][1]{tr.xfade(name, 0.6, 0.3)},format=yuv420p[v]"
    cmd = [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-y",
        *_two_clips(),
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        out,
    ]
    res = proc.run(cmd, timeout=120)
    assert res.ok, res.last_error_line()
    assert ff.probe(out, use_cache=False).duration == pytest.approx(1.3, abs=0.15)


def test_groups_hold_each_transition_once():
    names = [n for group in tr.GROUPS.values() for n in group]
    assert len(names) == len(set(names)) == len(tr.LABELS)
    assert set(tr.EVERYDAY) <= set(tr.LABELS) and not set(tr.EVERYDAY) & tr.SLOW


def test_unknown_transition_is_refused():
    with pytest.raises(ValueError, match="unknown transition"):
        tr.xfade("teleport", 1, 0)


def test_squares_change_square_by_square(tmp_path):
    """Halfway through, some squares show the second picture and others the
    first - not a uniform crossfade."""
    black, white = "color=black:s=160x90:r=10:d=1", "color=white:s=160x90:r=10:d=1"
    out = str(tmp_path / "mid.png")
    graph = f"[0][1]{tr.xfade('squares_big', 1.0, 0.0)},format=gray[v]"
    cmd = [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        black,
        "-f",
        "lavfi",
        "-i",
        white,
    ]
    cmd += [
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-ss",
        "0.5",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        out,
    ]
    assert proc.run(cmd, timeout=60).ok
    pixels = open(out, "rb").read()
    assert len(pixels) == 160 * 90
    dark = sum(1 for p in pixels if p < 40)
    light = sum(1 for p in pixels if p > 215)
    assert dark > 1000 and light > 1000  # both pictures visible, in whole squares
