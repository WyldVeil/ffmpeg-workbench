"""core/ops/contact_sheet.py: picking moments, the header, sizes, and a real sheet."""

from __future__ import annotations

import os

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core import ffmpeg as ff
from core import proc
from core.ops import contact_sheet as cs

durations = st.floats(min_value=1.0, max_value=20_000.0, allow_nan=False)
counts = st.integers(min_value=1, max_value=240)
variants = st.integers(min_value=-50, max_value=50)
skips = st.floats(min_value=0.0, max_value=60.0, allow_nan=False)


@given(durations, counts, variants, skips, skips)
def test_even_picks_are_in_order_and_inside_the_video(duration, count, variant, head, tail):
    times = cs.sample_times(
        duration, count, cs.Picking(skip_start=head, skip_end=tail, variant=variant)
    )
    assert len(times) == count
    assert times == sorted(times)
    assert all(0.0 <= t <= duration for t in times)


@given(durations, counts, variants)
def test_each_set_is_new(duration, count, variant):
    """Next set and previous set never show the same moments as this one."""
    here = cs.sample_times(duration, count, cs.Picking(variant=variant))
    for other in (variant - 1, variant + 1):
        there = cs.sample_times(duration, count, cs.Picking(variant=other))
        if duration / count > 0.05:  # distinguishable at millisecond rounding
            assert here != there


def test_the_first_set_is_the_middle_of_each_slot():
    assert cs.sample_times(100.0, 4, cs.Picking()) == [12.5, 37.5, 62.5, 87.5]


def test_skipping_the_start_and_end_leaves_out_titles_and_credits():
    times = cs.sample_times(100.0, 4, cs.Picking(skip_start=10, skip_end=10))
    assert times == [20.0, 40.0, 60.0, 80.0]


@given(durations, st.integers(min_value=1, max_value=60), variants, st.lists(st.floats(0, 20_000)))
def test_scene_picks_stay_in_their_slot(duration, count, variant, cuts):
    picking = cs.Picking(sampling=cs.Sampling.SCENES, variant=variant)
    times = cs.sample_times(duration, count, picking, sorted(cuts))
    step = duration / count
    for i, t in enumerate(times):
        # (the very end can't be grabbed: picks stop 50 ms before it)
        assert min(i * step, duration - 0.05) - 1e-3 <= t <= min((i + 1) * step, duration) + 1e-3


def test_scene_picks_land_just_after_a_cut_and_every_set_differs():
    picking = cs.Picking(sampling=cs.Sampling.SCENES)
    assert cs.sample_times(100.0, 2, picking, [10.0, 70.0]) == [10.5, 70.5]
    nxt = cs.sample_times(
        100.0, 2, cs.Picking(sampling=cs.Sampling.SCENES, variant=1), [10.0, 70.0]
    )
    assert nxt[0] != 10.5 and nxt[1] != 70.5


def test_cuts_are_read_from_the_log():
    log = [
        "[Parsed_metadata_2 @ 0x1] frame:0    pts:250     pts_time:10.01",
        "[Parsed_metadata_2 @ 0x1] lavfi.scene_score=0.51",
        "[Parsed_metadata_2 @ 0x1] frame:1    pts:700     pts_time:28",
        "[Parsed_metadata_2 @ 0x1] frame:1    pts:700     pts_time:28",
    ]
    assert cs.parse_cuts(log) == [10.01, 28.0]


@pytest.mark.parametrize(
    ("n", "text"),
    [(0, "0 bytes"), (1023, "1023 bytes"), (1536, "1.50 KB"), (5 * 1024**3, "5.00 GB")],
)
def test_sizes_read_naturally(n, text):
    assert cs.size_text(n) == text


def _info(**more) -> ff.MediaInfo:
    video = ff.Stream(
        0, "video", codec="hevc", profile="Main 10", width=3840, height=2160, fps=23.976
    )
    audio = ff.Stream(1, "audio", codec="eac3", sample_rate=48000, channel_layout="5.1(side)")
    base = {"path": "/films/Night Walk.mkv", "format_name": "matroska,webm", "duration": 5400.0}
    return ff.MediaInfo(**{**base, "size": 4 * 1024**3, "streams": (video, audio), **more})


def test_the_header_shows_what_was_ticked():
    lines = cs.header_lines(_info(), cs.Labels())
    assert lines[0] == "Night Walk.mkv"
    assert "4.00 GB" in lines[1] and "01:30:00" in lines[1] and "3840×2160" in lines[1]
    assert "23.976 fps" in lines[1]
    assert lines[2] == "Video: HEVC (Main 10)   ·   Audio: E-AC-3, 48 kHz, 5.1(side)"
    only = cs.header_lines(
        _info(), cs.Labels(details=frozenset({cs.Detail.RESOLUTION}), title="Rushes")
    )
    assert only == ["Rushes", "3840×2160"]
    assert cs.header_lines(_info(), cs.Labels(details=frozenset())) == []


def test_tiles_keep_the_picture_shape_and_the_sheet_adds_up():
    layout = cs.Layout(cols=3, rows=2, tile_width=301, gap=7, margin=10, border=2)
    geo = cs.geometry(_info(), layout, header=50)
    assert (geo.tile_w, geo.tile_h) == (300, 168)
    assert geo.width % 2 == 0 and geo.width >= 2 * 10 + 3 * 304 + 2 * 7
    assert geo.height >= 10 + 50 + 2 * 172 + 7 + 10
    assert geo.cell(layout, 4) == (10 + 304 + 7 + 2, 60 + 172 + 7 + 2)


def _run(cmd: list[str], _done: float) -> bool:
    return proc.run(cmd, timeout=60).ok


@pytest.mark.parametrize(
    ("name", "ext"),
    [("h264", "png"), ("vertical_noaudio", "jpg"), ("hevc10", "webp"), ("nasty", "png")],
)
def test_a_real_sheet(tmp_path, media, name, ext):
    info = ff.probe(media[name])
    layout = cs.Layout(cols=3, rows=2, tile_width=160, border=2)
    labels = cs.Labels(numbers=True, timestamps=cs.Corner.TOP_LEFT)
    p = cs.plan(info, layout, labels, cs.Picking(variant=3))
    out = str(tmp_path / f"sheet.{ext}")
    cs.make(p, str(tmp_path), out, _run)
    sheet = ff.probe(out, use_cache=False).require_video()
    assert (sheet.width, sheet.height) == (p.geo.width, p.geo.height)
    assert os.path.getsize(out) > 1000


def test_a_moment_that_cant_be_read_gets_a_plain_tile(tmp_path, media):
    info = ff.probe(media["h264"])
    p = cs.plan(info, cs.Layout(cols=2, rows=1, tile_width=96), cs.Labels(), cs.Picking())
    late = cs.Plan(p.src, (1.0, info.duration + 30), p.geo, p.layout, p.labels, p.lines)
    done: list[float] = []

    def run(cmd: list[str], fraction: float) -> bool:
        done.append(fraction)
        return _run(cmd, fraction)

    cs.make(late, str(tmp_path), str(tmp_path / "sheet.png"), run)
    assert os.path.getsize(tmp_path / "sheet.png") > 500
    assert done == sorted(done) and done[-1] == 1.0


def test_a_cancelled_sheet_stops(tmp_path, media):
    p = cs.plan(ff.probe(media["h264"]), cs.Layout(cols=2, rows=2), cs.Labels(), cs.Picking())
    with pytest.raises(ff.CancelledError):
        cs.make(p, str(tmp_path), str(tmp_path / "sheet.png"), _run, cancelled=lambda: True)
