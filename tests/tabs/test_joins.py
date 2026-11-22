"""core.concat + Batch Joiner, Splicer and Multi-Splitter end to end."""
import os

import pytest

from core import concat
from core import ffmpeg as ff


@pytest.mark.ffmpeg
def test_copy_compatibility(media):
    same = [ff.probe(media["h264"]), ff.probe(media["nasty"])]
    assert concat.copy_compatibility(same) == []
    diff = concat.copy_compatibility([ff.probe(media["h264"]), ff.probe(media["h264_1080p25"])])
    assert diff and "size" in diff[0] and "frame rate" in diff[0]
    assert concat.copy_compatibility([ff.probe(media["h264"]), ff.probe(media["vertical_noaudio"])])


@pytest.mark.ffmpeg
def test_filter_join_normalises_and_fills_silence(media, outdir):
    pieces = [concat.Piece.of(media["h264"], 1.0, 3.0), concat.Piece.of(media["vertical_noaudio"], 0, 2.0),
              concat.Piece.of(media["hevc10"], 2.0, None)]
    out = str(outdir / "j.mp4")
    cmd, seconds = concat.filter_join_command(pieces, out)
    assert seconds == pytest.approx(7.0, abs=0.05)
    res = ff.run(cmd, duration=seconds)
    assert res.ok, res.error_summary
    info = ff.probe(out, use_cache=False)
    assert info.duration == pytest.approx(7.0, abs=0.15)
    assert (info.width, info.height) == (1280, 720) and info.has_audio
    assert abs(info.require_video().duration - info.require_audio().duration) < 0.1


def _add_files(ui, tab, paths):
    ui.answer_open_many(paths)
    ui.click_text(tab, "Add Files")


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_batch_joiner_offers_reencode_for_mismatched_files(ui, media, outdir):
    tab = ui.open("Batch Joiner")
    tab._clear()
    _add_files(ui, tab, [media["h264"], media["h264_1080p25"]])
    out = str(outdir / "joined.mp4")
    tab.out_var.set(out)
    tab.reencode_var.set(False)
    ui.click_text(tab, "JOIN")                 # stubbed askyesno -> yes, re-encode
    ui.wait_idle(180)
    assert any(d[0] == "askyesno" for d in ui.dialogs.log)
    ui.assert_media(out, video=True, audio=True, min_duration=10.8, max_duration=11.2)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_batch_joiner_lossless_with_hostile_names(ui, media, outdir):
    tab = ui.open("Batch Joiner")
    tab._clear()
    _add_files(ui, tab, [media["h264"], media["nasty"]])
    out = str(outdir / "lossless.mp4")
    tab.out_var.set(out)
    tab.reencode_var.set(False)
    ui.click_text(tab, "JOIN")
    ui.wait_idle(120)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    ui.assert_media(out, video=True, audio=True, min_duration=11.8, max_duration=12.2)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_batch_joiner_refuses_to_overwrite_an_input(ui, media, tmp_path):
    src = tmp_path / "a.mp4"
    import shutil
    shutil.copy(media["h264"], src)
    tab = ui.open("Batch Joiner")
    tab._clear()
    _add_files(ui, tab, [str(src), media["h264_b"]])
    tab.out_var.set(str(src))
    ui.click_text(tab, "JOIN")
    ui.wait_idle(30)
    assert any("same as an input" in d[2] for d in ui.dialogs.warnings())
    assert os.path.getsize(src) == os.path.getsize(media["h264"])


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_splicer_segments_from_mixed_sources(ui, media, outdir):
    tab = ui.open("The Splicer")
    for _ in range(len(tab.segments)):
        tab._remove_last()
    for path, start, end in ((media["h264"], "00:01", "00:03"), (media["vertical_noaudio"], "0", "2")):
        ui.answer_open(path)
        tab._add_segment()
        tab.segments[-1]["start"].set(start)
        tab.segments[-1]["end"].set(end)
    out = str(outdir / "spliced.mp4")
    tab.out_var.set(out)
    ui.click_text(tab, "SPLICE")
    ui.wait_idle(180)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    ui.assert_media(out, video=True, audio=True, min_duration=3.9, max_duration=4.15)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_multi_splitter_reports_and_names(ui, media, tmp_path):
    import shutil
    src = tmp_path / "source clip.mp4"
    shutil.copy(media["h264"], src)
    tab = ui.open("Manual Multi-Splitter")
    ui.answer_open(str(src))
    ui.click_text(tab, "Browse")
    for r in list(tab.split_rows):
        tab._remove_row(r["frame"])
    for a, b in (("0", "2"), ("00:02", "00:04.5")):
        tab._add_row()
        tab.split_rows[-1]["start"].set(a)
        tab.split_rows[-1]["end"].set(b)
    tab.copy_mode.set(False)
    ui.click_text(tab, "EXTRACT")
    ui.wait_idle(180)
    outs = sorted(p for p in os.listdir(tmp_path) if p != "source clip.mp4")
    assert outs == ["source clip_clip1.mp4", "source clip_clip2.mp4"], outs
    ui.assert_media(str(tmp_path / outs[1]), min_duration=2.4, max_duration=2.6)
    assert ui.dialogs.log[-1][0] == "showinfo"
    # duplicate suffixes are refused up-front
    tab.split_rows[1]["suffix"].set(tab.split_rows[0]["suffix"].get())
    ui.click_text(tab, "EXTRACT")
    assert "different suffixes" in ui.dialogs.warnings()[-1][2]
