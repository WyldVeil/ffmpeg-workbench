"""Tests for core/filters.py - escaping verified by real FFmpeg runs on a
file name full of filtergraph metacharacters."""

import os
import shutil

import pytest

from core import ffmpeg as ff
from core import filters as fl

NASTY = "a b's [x], c;d: 50% \\e.srt"


def test_filter_value_escapes_both_levels():
    assert fl.filter_value("a:b") == "a\\\\:b"
    assert fl.filter_value("it's") == "it\\\\\\'s"
    assert fl.filter_value("[x],y;z") == "\\[x\\]\\,y\\;z"


def test_hex_to_ass_colour():
    assert fl.hex_to_ass_colour("#FF8800") == "&H000088FF"
    assert fl.hex_to_ass_colour("00ff00", alpha=0x80) == "&H8000FF00"


def test_concat_line_quotes():
    path = os.path.abspath("/tmp/it's here.mp4")  # a drive letter and backslashes on Windows
    line = fl.concat_line(path)
    assert line == "file '" + path.replace("'", "'\\''") + "'"
    assert line.endswith("it'\\''s here.mp4'")


def test_write_ffmetadata(tmp_path):
    p = fl.write_ffmetadata(
        str(tmp_path / "m.txt"),
        chapters=[(0, 1.5, "Intro; part=1 #a"), (1.5, 3, "B\\C")],
        tags={"title": "T=1"},
    )
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    assert text.startswith(";FFMETADATA1\n")
    assert "title=T\\=1" in text
    assert "title=Intro\\; part\\=1 \\#a" in text and "START=1500" in text


@pytest.fixture
def nasty_dir(tmp_path):
    d = tmp_path / "dir with 'quote' [b] & c, 100%"
    d.mkdir()
    return d


@pytest.mark.ffmpeg
def test_subtitles_filter_with_hostile_path(media, nasty_dir, outdir):
    srt = nasty_dir / NASTY.replace("\\", "_")
    shutil.copy(media["srt"], srt)
    vf = fl.subtitles_filter(str(srt), force_style=fl.ass_style(font="Inter", size=28))
    out = str(outdir / "subbed.mp4")
    res = ff.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            media["h264"],
            "-t",
            "3",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-an",
            out,
        ]
    )
    assert res.ok, res.error_summary


@pytest.mark.ffmpeg
def test_lut3d_and_movie_with_hostile_path(media, nasty_dir, outdir):
    cube = nasty_dir / "warm 'lut' [1], x;y.cube"
    shutil.copy(media["cube"], cube)
    logo = nasty_dir / "logo 'a' [b], c.png"
    shutil.copy(media["png"], logo)
    fc = (
        f"{fl.movie_source(str(logo))},scale=160:-2[lg];"
        f"[0:v]{fl.lut3d_filter(str(cube))}[g];[g][lg]overlay=10:10[v]"
    )
    out = str(outdir / "lut.mp4")
    res = ff.run(
        [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            media["h264"],
            "-t",
            "2",
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            out,
        ]
    )
    assert res.ok, res.error_summary


@pytest.mark.ffmpeg
@pytest.mark.parametrize("at", [0.0, 1.5])
def test_preview_with_a_still_image_later_in_the_video(media, nasty_dir, at):
    """A logo / background photo in a preview must still be there after the
    first frame (previews keep the source timestamps)."""
    logo = nasty_dir / "logo 'a' [b], c.png"
    shutil.copy(media["png"], logo)
    vf = f"{fl.preview_movie(str(logo))}scale=160:-2[lg];[in][lg]overlay=10:10:shortest=1"
    assert ff.grab_frame_png(media["h264"], at, width=320, height=180, vf=vf, keep_time=True)


@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "text", ["Hello: it's 50% off, [now]; ok?", "Ünïcödé — 日本語 \\ back", "two\nlines"]
)
def test_drawtext_textfile_and_inline(text, media, outdir, tmp_path):
    tf = fl.drawtext_text_file(text, directory=str(tmp_path))
    for vf in (
        fl.drawtext_filter(
            textfile=tf, font="sans", fontsize=36, box=True, enable=fl.enable_between(0.5, 1.5)
        ),
        fl.drawtext_filter(
            text=text, font="sans bold", fontsize="h/12", borderw=2, alpha="if(lt(t,1),t,1)"
        ),
    ):
        out = str(outdir / "dt.mp4")
        res = ff.run(
            [
                ff.ffmpeg_path(),
                "-y",
                "-i",
                media["h264"],
                "-t",
                "2",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-an",
                out,
            ]
        )
        assert res.ok, f"{vf}\n{res.error_summary}"


@pytest.mark.ffmpeg
def test_concat_list_with_hostile_names(media, nasty_dir, outdir):
    a = nasty_dir / "it's [a], 1;2.mp4"
    b = nasty_dir / "b 50% off.mp4"
    shutil.copy(media["h264"], a)
    shutil.copy(media["h264_b"], b)
    lst = fl.write_concat_list([str(a), str(b)], str(outdir / "list.txt"))
    out = str(outdir / "joined.mp4")
    res = ff.run(
        [ff.ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out]
    )
    assert res.ok, res.error_summary
    assert ff.probe(out, use_cache=False).duration == pytest.approx(10.0, abs=0.3)


def test_find_font_returns_existing_file():
    for name in ("sans", "sans bold", "Montserrat-Bold", "Arial", "impact", "mono"):
        path = fl.find_font(name)
        assert path is None or os.path.isfile(path), name
    assert fl.find_font("sans") is not None
