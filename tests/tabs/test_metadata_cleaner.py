"""Metadata Cleaner: finds personal metadata and removes it without re-encoding."""

import subprocess

import pytest

from core import ffmpeg as ff
from core import metaclean as mc


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness
    return Harness.instance()


@pytest.fixture
def phone_video(tmp_path):
    """A 'phone' clip: GPS, make/model, title, date, rotation, chapter, cover, English audio."""
    tmp = tmp_path / "raw.mp4"
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=30",
                    "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", "libx264", "-c:a", "aac", str(tmp)], check=True)
    meta = tmp_path / "meta.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=Alice's birthday\n")
    out = tmp_path / "phone.mp4"
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-display_rotation", "90", "-i", str(tmp),
                    "-i", str(meta), "-map", "0", "-map_chapters", "1", "-c", "copy", "-movflags", "use_metadata_tags",
                    "-metadata", "title=Holiday with Alice", "-metadata", "location=+51.5074-000.1278/",
                    "-metadata", "com.apple.quicktime.make=Apple", "-metadata", "com.apple.quicktime.model=iPhone 17",
                    "-metadata", "creation_time=2026-07-04T12:00:00Z", "-metadata:s:a:0", "language=eng",
                    str(out)], check=True)
    return str(out)


def test_inspect_finds_the_personal_bits(phone_video):
    found = mc.inspect(ff.probe(phone_video)) + mc.stream_notes(phone_video)
    cats = {f.category for f in found}
    assert {"location", "device", "text", "chapters"} <= cats
    assert any("x264" in f.label for f in found)                       # settings written inside the stream


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("deep", [False, True])
def test_clean_is_lossless_and_complete(ui, phone_video, deep):
    ui.reset()
    tab = ui.open("Metadata Cleaner")
    tab._clear()
    tab.load_file(phone_video)
    tab.deep.set(deep)
    tab._clean()
    ui.wait_idle(120)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    out = phone_video.replace(".mp4", " (clean).mp4")
    before, after = ff.probe(phone_video), ff.probe(out, use_cache=False)
    left = mc.leftovers(mc.inspect(after) + (mc.stream_notes(out) if deep else []))
    assert left == [], [f.line() for f in left]
    assert after.video.rotation == before.video.rotation != 0           # still the right way up
    assert after.audio.tags.get("language") == "eng"
    assert after.video.codec == before.video.codec and abs(after.duration - before.duration) < 0.05
    raw = open(out, "rb").read()
    for secret in (b"Alice", b"iPhone", b"51.5074", b"Lavf"):
        assert secret not in raw
    if deep:
        assert b"x264 - core" not in raw
    # the video data itself is untouched (no re-encode): same packet count and sizes
    def packets(p):
        r = subprocess.run([ff.ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "packet=size", "-of", "csv=p=0", p], capture_output=True, text=True, check=True)
        return r.stdout.split()
    if not deep:
        assert packets(out) == packets(phone_video)
    else:
        assert len(packets(out)) == len(packets(phone_video))
