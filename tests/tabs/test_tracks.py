"""Tracks & Remux: keep/drop/reorder/label tracks, add tracks, change container, extract."""
import json
import os
import subprocess

import pytest

from core import ffmpeg as ff
from core import remux

ASS = ("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
       "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
       "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,20,"
       "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n\n[Events]\nFormat: Layer, "
       "Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
       "Dialogue: 0,0:00:00.50,0:00:02.00,Default,,0,0,0,,Bonjour\n")


@pytest.fixture(scope="module")
def multi(media, tmp_path_factory):
    """Video, English AAC (default), Spanish Opus, English SRT, French ASS, an attached font."""
    d = tmp_path_factory.mktemp("tracks")
    srt, ass = d / "en.srt", d / "fr.ass"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nHello\n", encoding="utf-8")
    ass.write_text(ASS, encoding="utf-8")
    font = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts", "Anton.ttf")
    out = str(d / "multi.mkv")
    ff.check([ff.ffmpeg_path(), "-y", "-v", "error", "-i", media["h264"], "-f", "lavfi", "-t", "6", "-i",
              "sine=frequency=880:sample_rate=48000", "-i", str(srt), "-i", str(ass),
              "-map", "0:v", "-map", "0:a", "-map", "1:a", "-map", "2", "-map", "3", "-c:v", "copy", "-c:a:0", "copy",
              "-c:a:1", "libopus", "-c:s", "copy", "-metadata:s:a:0", "language=eng", "-metadata:s:a:1",
              "language=spa", "-metadata:s:a:1", "title=Spanish dub", "-metadata:s:s:0", "language=eng",
              "-metadata:s:s:1", "language=fra", "-disposition:a:0", "default", "-disposition:a:1", "0",
              "-attach", os.path.abspath(font), "-metadata:s:t", "mimetype=font/ttf", out])
    return out


def streams(path):
    r = subprocess.run([ff.ffprobe_path(), "-v", "error", "-show_entries",
                        "stream=index,codec_type,codec_name,codec_tag_string,start_time:stream_tags=language,title,"
                        "filename:stream_disposition=default,forced:format_tags=title", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_container_rules():
    def tr(kind, codec):
        return remux.Track(0, 0, kind, codec)
    assert remux.problems([tr("subtitle", "hdmv_pgs_subtitle")], "mp4")[0][1].endswith("(use MKV to keep them)")
    assert "converted" in remux.problems([tr("subtitle", "ass")], "mp4")[0][1]
    assert "can't go into MP4" in remux.problems([tr("audio", "pcm_s24le")], "mp4")[0][1]
    assert remux.problems([tr("audio", "pcm_s24le")], "mov") == []
    assert "only go into MKV" in remux.problems([tr("attachment", "ttf")], "mp4")[0][1]
    assert remux.problems([tr("subtitle", "hdmv_pgs_subtitle"), tr("attachment", "ttf")], "mkv") == []
    with pytest.raises(ValueError):
        remux.remux_command(["a.mkv"], [tr("subtitle", "subrip")], "out.mkv")


@pytest.mark.ffmpeg
def test_lists_tracks(multi):
    plan = remux.Plan()
    plan.load(multi)
    kinds = [(t.kind, t.codec, t.language) for t in plan.tracks]
    assert kinds == [("video", "h264", ""), ("audio", "aac", "eng"), ("audio", "opus", "spa"),
                     ("subtitle", "subrip", "eng"), ("subtitle", "ass", "fra"), ("attachment", "ttf", "")]
    assert plan.tracks[1].default and not plan.tracks[2].default
    assert "Anton.ttf" in plan.tracks[5].describe() and "TTF" in plan.tracks[5].describe()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_remux_mkv_keeps_choices(ui, multi, outdir):
    ui.reset()
    tab = ui.open("Tracks & Remux")
    tab.load_file(multi)
    ui.pump(0.2)
    assert len(tab.tree.get_children()) == 6
    tab.tree.selection_set("2")                       # Spanish audio: drop it
    tab._toggle_keep()
    tab.tree.selection_set("4")                       # French ASS: first subtitle, default + forced
    tab._move(-1)
    tab.default_var.set(True)
    tab.forced_var.set(True)
    tab._apply_edit()
    tab.title_var.set("Français")
    tab._apply_edit()
    tab.file_title.set("My film")
    out = str(outdir / "remux.mkv")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    assert not ui.dialogs.errors()
    data = streams(out)
    got = [(s["codec_type"], s.get("codec_name"), s.get("tags", {}).get("language", "und")) for s in data["streams"]]
    assert got[:4] == [("video", "h264", "und"), ("audio", "aac", "eng"), ("subtitle", "ass", "fra"),
                       ("subtitle", "subrip", "eng")]
    assert got[4][0] == "attachment"                   # the font stays with the ASS subtitles
    fr = data["streams"][2]
    assert fr["disposition"]["default"] == 1 and fr["disposition"]["forced"] == 1
    assert fr["tags"]["title"] == "Français" and data["format"]["tags"]["title"] == "My film"


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_remux_to_mp4_explains_and_converts(ui, multi, outdir):
    ui.reset()
    tab = ui.open("Tracks & Remux")
    tab.plan.inputs = []                               # force a fresh load
    tab.load_file(multi)
    tab.container_var.set("MP4 - plays everywhere")
    tab._container_changed()
    notes = tab.notes.cget("text")
    assert "converted to MP4 text subtitles" in notes and "only go into MKV" in notes
    out = str(outdir / "remux.mp4")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    assert not ui.dialogs.errors()
    data = streams(out)
    types = [(s["codec_type"], s.get("codec_name")) for s in data["streams"]]
    assert types == [("video", "h264"), ("audio", "aac"), ("audio", "opus"), ("subtitle", "mov_text"),
                     ("subtitle", "mov_text")]


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_add_track_with_delay_and_extract(ui, multi, media, outdir):
    ui.reset()
    tab = ui.open("Tracks & Remux")
    tab.plan.inputs = []
    tab.load_file(multi)
    tab.add_file(media["mp3"], delay=0.5, language="deu")
    ui.pump(0.1)
    assert tab.plan.tracks[-1].language == "deu" and tab.plan.tracks[-1].delay == 0.5
    out = str(outdir / "added.mkv")
    tab.out_row.set(out)
    tab._render()
    ui.wait_idle(120)
    data = streams(out)
    added = next(s for s in data["streams"] if s.get("tags", {}).get("language") == "deu")
    assert added["codec_name"] == "mp3" and float(added["start_time"]) == pytest.approx(0.5, abs=0.05)
    # save the French subtitles and the font on their own
    tab.extract(4, str(outdir / "fr.ass"))
    ui.wait_idle(60)
    assert "Bonjour" in open(outdir / "fr.ass", encoding="utf-8").read()
    tab.extract(5, str(outdir / "font.ttf"))
    ui.wait_idle(60)
    assert os.path.getsize(outdir / "font.ttf") > 10_000


@pytest.mark.ffmpeg
def test_cli_remux_and_extract(capsys, multi, media, outdir):
    from core import cli

    def run(*argv):
        assert cli.main([*argv, "--json"]) == 0, capsys.readouterr()
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    res = run("remux", multi, "--to", "mp4", "--keep-languages", "eng", "--default-subtitles", "eng",
              "--add", f"{media['mp3']}:deu:0.25", "-o", str(outdir / "cli.mp4"))
    data = streams(res["output"])
    langs = [(s["codec_type"], s.get("tags", {}).get("language")) for s in data["streams"]]
    assert langs == [("video", "und"), ("audio", "eng"), ("subtitle", "eng"), ("audio", "deu")]
    assert data["streams"][2]["disposition"]["default"] == 1
    res = run("extract-track", multi, "--track", "3", "-o", str(outdir / "en.srt"))
    assert "Hello" in open(res["output"], encoding="utf-8").read()
    res = run("extract-track", multi, "--track", "2", "-o", str(outdir))
    assert res["output"].endswith("_spa.opus") and res["audio"].startswith("opus")


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_editor_changes_stay_with_their_track(ui, multi):
    """Typing a title then clicking another row: the title belongs to the first row
    (the selection moves before the title box loses focus)."""
    ui.reset()
    tab = ui.open("Tracks & Remux")
    tab.plan.inputs = []
    tab.load_file(multi)
    tab.tree.selection_set("2")
    ui.pump(0.1)
    tab.title_var.set("Commentary")                    # typed, not yet saved
    tab.tree.selection_set("3")                        # click another row...
    ui.pump(0.1)
    tab._apply_edit()                                  # ...then the title box's focus-out arrives
    assert tab.plan.tracks[2].title == "Commentary"
    assert tab.plan.tracks[3].title == ""
    assert tab.title_var.get() == ""                   # the editor now shows row 3
    tab.lang_var.set("(not set)")
    tab._apply_edit()
    assert tab.plan.tracks[3].language == ""
    tab.lang_var.set("German")
    tab._apply_edit()
    assert tab.plan.tracks[3].language == "deu"
