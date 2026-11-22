"""Codec Cruncher, Audio Sync Shifter and every Special Effect, rendered for real
(these tools had no render tests - Codec Cruncher didn't even open)."""
import os
import re

import pytest

from core import ffmpeg as ff
from tabs.transcoder import codec as cc

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ok(ui):
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    assert not ui.dialogs.warnings(), ui.dialogs.warnings()


# ── Codec Cruncher ───────────────────────────────────────────────────────────

def _cruncher(ui, src, out, *, codec, container="auto", audio="aac256", copy_video=False, ten_bit=False):
    ui.reset()
    tab = ui.open("Codec Cruncher")
    tab.src_row.set(src)
    tab._source_changed(src)
    tab.copy_video.set(copy_video)
    tab.encoder.set_codec(codec)
    tab.encoder._ten_bit.set(ten_bit)
    tab.container_var.set(container)
    tab.audio_var.set(cc.AUDIO[audio][0])
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(300)
    _ok(ui)
    return ff.probe(out, use_cache=False)


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_cruncher_reencodes(ui, media, outdir):
    info = _cruncher(ui, media["h264"], str(outdir / "c.mkv"), codec="hevc", container="mkv", audio="opus")
    assert (info.require_video().codec, info.require_audio().codec) == ("hevc", "opus")
    assert info.duration == pytest.approx(6.0, abs=0.1)
    info = _cruncher(ui, media["h264_b"], str(outdir / "p.mov"), codec="prores", audio="pcm24")
    assert (info.require_video().codec, info.require_audio().codec) == ("prores", "pcm_s24le")


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_cruncher_copies_video(ui, media, outdir):
    info = _cruncher(ui, media["vp9"], str(outdir / "v.mkv"), codec="h264", container="mkv", audio="copy",
                     copy_video=True)
    assert (info.require_video().codec, info.require_audio().codec) == ("vp9", "opus")


# ── Audio Sync Shifter ───────────────────────────────────────────────────────

@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("offset,lossless", [(0.5, True), (-0.3, False), (0.25, False)])
def test_sync_shifter(ui, media, outdir, offset, lossless):
    ui.reset()
    tab = ui.open("Audio Sync Shifter")
    tab.src_row.set(media["h264"])
    tab._source_changed(media["h264"])
    ui.pump(0.2)
    tab.offset_var.set(f"{offset:.3f}")
    tab.lossless_var.set(lossless)
    out = str(outdir / f"sync_{offset}_{lossless}.mkv")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    if lossless:
        # the sound's timestamps start later; nothing re-encoded
        assert info.require_audio().start_time - info.require_video().start_time == pytest.approx(offset, abs=0.05)
        assert info.require_audio().codec == ff.probe(media["h264"]).audio.codec
    elif offset > 0:
        assert _mean_db(out, 0.0, offset - 0.05) < -60          # silence in front...
        assert _mean_db(out, 1.0, 1.0) > -30                    # ...then the sound
    else:
        assert _mean_db(out, 6.0 + offset + 0.05, -offset - 0.1) < -60   # starts earlier: silence at the end
        assert _mean_db(out, 1.0, 1.0) > -30


def _mean_db(path, start, dur):
    r = ff.capture([ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i",
                    path, "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"], timeout=60)
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf)", r.stderr or "")
    return float(m.group(1)) if m and m.group(1) != "-inf" else -200.0


# ── Special Effects: every effect ────────────────────────────────────────────

def _effects():
    code = open(os.path.join(ROOT, "tabs", "visuals", "effects.py"), encoding="utf-8").read()
    out = []
    for m in re.finditer(r"    def (_render_\w+)\(self\):(.*?)(?=\n    def |\Z)", code, re.S):
        src = re.search(r"self\.(_\w+?)_src\b", m.group(2))
        dst = re.search(r"self\.(_\w+?)_out\b", m.group(2))
        out.append((m.group(1), src.group(1), dst.group(1)))
    return out


EFFECTS = _effects()


def test_found_every_effect():
    assert len(EFFECTS) >= 25


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("render,src_attr,out_attr", EFFECTS, ids=[e[0] for e in EFFECTS])
@pytest.mark.parametrize("source", ["h264", "vertical_noaudio"])
def test_every_effect_renders(ui, media, outdir, render, src_attr, out_attr, source):
    ui.reset()
    tab = ui.open("Special Effects")
    out = str(outdir / f"{render}.mp4")
    getattr(tab, src_attr + "_src").set(media[source])
    getattr(tab, out_attr + "_out").set(out)
    getattr(tab, render)()
    ui.wait_idle(240)
    _ok(ui)
    info = ff.probe(out, use_cache=False)
    src = ff.probe(media[source])
    assert info.require_video().width % 2 == 0 and info.require_video().pix_fmt == "yuv420p"
    assert info.duration >= src.duration - 0.2
    assert info.has_audio == src.has_audio
