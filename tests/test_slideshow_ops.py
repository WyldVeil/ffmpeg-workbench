"""core/ops/slideshow.py: timing, music, the graphs, and a real mixed slideshow."""

from __future__ import annotations

import os

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core import ffmpeg as ff
from core import proc
from core.ops import slideshow as ss

FADE, CUT = ss.Join("fade", 1.0), ss.Join(ss.CUT)


def test_transitions_overlap_and_cuts_do_not():
    starts, total = ss.timeline([4, 4, 4], [FADE, CUT])
    assert starts == [0, 3, 7] and total == 11


def test_a_transition_too_long_for_its_slides_is_explained():
    with pytest.raises(ValueError, match="after slide 1"):
        ss.check_joins([4, 1.5], [FADE])
    ss.check_joins([4, 4], [FADE])


def test_fitting_to_the_music_shares_what_is_left():
    # 30 s of music, a 6 s video kept, two transitions of 1 s: the photos share 26 s
    lengths = ss.fit_to_music([None, 6.0, None], [FADE, FADE], 30.0)
    assert lengths == [13.0, 6.0, 13.0]
    assert ss.timeline(lengths, [FADE, FADE])[1] == pytest.approx(30.0)


lengths_st = st.lists(st.floats(min_value=1.5, max_value=20), min_size=2, max_size=12)


@given(lengths_st, st.lists(st.floats(min_value=0, max_value=200), max_size=200))
def test_beats_never_make_a_slide_too_short(lengths, beats):
    joins = [FADE if i % 2 else CUT for i in range(len(lengths) - 1)]
    out = ss.on_beats(lengths, joins, sorted(beats))
    assert len(out) == len(lengths)
    for i, s in enumerate(out):
        needed = (
            0.5 + (joins[i - 1].overlap if i else 0) + (joins[i].overlap if i < len(joins) else 0)
        )
        assert s >= needed - 1e-6


def test_changes_land_on_the_beat():
    beats = [i * 0.5 for i in range(100)]  # 120 BPM
    out = ss.on_beats([3.3, 3.3, 3.3, 3.3], [CUT, CUT, CUT], beats)
    starts, _total = ss.timeline(out, [CUT, CUT, CUT])
    assert all(abs(s * 2 - round(s * 2)) < 1e-9 for s in starts)


def test_music_repeats_until_the_slideshow_ends_and_starts_at_the_offset():
    music = ss.Music(tracks=("a.mp3", "b.mp3"), lengths=(10.0, 5.0), offset=4.0)
    # 6 s of a (from 4 s in), 5 of b, 10 of a, 5 of b = 26 s: one more a to reach 30
    assert ss.music_inputs(music, 30) == [
        ("a.mp3", 4.0),
        ("b.mp3", 0.0),
        ("a.mp3", 0.0),
        ("b.mp3", 0.0),
        ("a.mp3", 0.0),
    ]


def test_each_pair_gets_its_own_join():
    parts = ss.join_graph([ss.Join("squares", 1.0), CUT], [0, 3, 7])
    assert "xfade=transition=custom" in parts[3] and "offset=3.000" in parts[3]
    assert parts[-1] == "[x1][v2]concat=n=2:v=1:a=0[xf]"


def _slides(sound: bool) -> list[ss.Slide]:
    return [
        ss.Slide(kind=ss.Kind.PHOTO, path="p.jpg", seconds=4),
        ss.Slide(kind=ss.Kind.VIDEO, path="v.mp4", seconds=4, sound=sound),
    ]


def test_music_is_quieter_while_a_clip_speaks():
    music = ss.Music(tracks=("m.mp3",), lengths=(60.0,))
    parts, label = ss.audio_graph(_slides(True), [FADE], music, 2)
    assert label == "[a]" and any("sidechaincompress" in p for p in parts)
    assert any(p.startswith("[1:a]") and "adelay=3000|3000" in p for p in parts)
    parts, label = ss.audio_graph(_slides(False), [FADE], None, 2)
    assert (parts, label) == ([], None)
    parts, label = ss.audio_graph(_slides(False), [FADE], music, 2)
    assert label == "[a]" and not any("sidechain" in p for p in parts)


def _run(cmd: list[str]) -> None:
    res = proc.run(cmd, timeout=300)
    assert res.ok, res.last_error_line()


@pytest.mark.ffmpeg
def test_a_real_slideshow_of_every_kind(tmp_path, media):
    gif = str(tmp_path / "loop.gif")
    _run([ff.ffmpeg_path(), "-y", "-f", "lavfi", "-i", "testsrc2=s=200x150:r=10:d=1", gif])
    look = ss.Look(640, 360, "25", fit="bars", background="#203040")
    slides = [
        ss.Slide(kind=ss.Kind.CARD, seconds=2, card=ss.Card("Our trip", "Summer 2026")),
        ss.Slide(
            kind=ss.Kind.PHOTO, path=media["png"], seconds=2.5, motion="zoom_in", caption="Day one"
        ),
        ss.Slide(kind=ss.Kind.ANIMATION, path=gif, seconds=2.5),  # longer than the GIF: it loops
        ss.Slide(kind=ss.Kind.VIDEO, path=media["h264"], seconds=2, start=1.0, sound=True),
    ]
    joins = [ss.Join("squares", 0.6), CUT, ss.Join("swirl", 0.4)]
    ss.check_joins([s.seconds for s in slides], joins)
    clips = []
    for i, slide in enumerate(slides):
        clip = ss.clip_name(str(tmp_path), i, slide)
        card = (
            ss.card_script(slide.card, look).write(str(tmp_path / f"c{i}.ass"))
            if slide.card
            else ""
        )
        _run(ss.slide_cmd(slide, look, clip, card))
        clips.append(clip)
    captions = ss.caption_script(slides, joins, look, ("Montserrat", "bottom")).write(
        str(tmp_path / "cap.ass")
    )
    music = ss.Music(tracks=(media["mp3"],), lengths=(ff.duration_of(media["mp3"]),), offset=0.5)
    out = str(tmp_path / "show.mp4")
    _run(
        ss.join_cmd(clips, slides, joins, out, enc=ss.Encode(), music=music, captions_ass=captions)
    )
    info = ff.probe(out, use_cache=False)
    total = ss.timeline([s.seconds for s in slides], joins)[1]
    assert info.duration == pytest.approx(total, abs=0.15)
    assert (info.width, info.height) == (640, 360) and info.has_audio
    assert os.path.getsize(out) > 20_000
