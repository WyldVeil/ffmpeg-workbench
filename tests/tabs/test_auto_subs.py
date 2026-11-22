"""Auto-Subtitles with the bundled whisper.cpp engine.

Needs a speech sample and a model, which are too big for the repo: set
CROSSFADER_TEST_WHISPER_DIR to a folder holding ``ggml-base-q5_1.bin`` and
``jfk.wav`` (whisper.cpp's sample).  Skipped otherwise."""
import os
import shutil

import pytest

from core import ffmpeg as ff
from core import whisper
from core.tools import ToolId

ASSETS = os.environ.get("CROSSFADER_TEST_WHISPER_DIR", "")


def test_commands_and_progress():
    cmd = whisper.transcribe_cmd("a.wav", "m.bin", "/tmp/out", language="de", translate=True,
                                 max_len=40, formats=("srt", "vtt"), prompt="WyldVeil")
    assert cmd[0].endswith(("whisper-cli", "whisper-cli.exe"))
    for flag in ("-osrt", "-ovtt", "-tr", "-sow", "--prompt"):
        assert flag in cmd
    assert cmd[cmd.index("-l") + 1] == "de"
    assert whisper.parse_progress("whisper_print_progress_callback: progress = 45%") == 0.45
    assert whisper.parse_progress("nothing") is None


needs_assets = pytest.mark.skipif(
    not (ASSETS and os.path.isfile(os.path.join(ASSETS, "ggml-base-q5_1.bin"))
         and os.path.isfile(os.path.join(ASSETS, "jfk.wav"))),
    reason="set CROSSFADER_TEST_WHISPER_DIR (ggml-base-q5_1.bin + jfk.wav)")


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness
    return Harness.instance()


@needs_assets
@pytest.mark.gui
@pytest.mark.ffmpeg
def test_transcribe_mux_and_burn(ui, outdir, media):
    shutil.copyfile(os.path.join(ASSETS, "ggml-base-q5_1.bin"), whisper.model_path("base"))
    with open(whisper.model_path("base") + ".ok", "w") as fh:
        fh.write("test\n")
    # a video with JFK's speech as its audio track
    video = str(outdir / "jfk talk.mp4")
    ff.check([ff.ffmpeg_path(), "-y", "-f", "lavfi", "-i", "color=c=0x334455:s=640x360:d=11",
              "-i", os.path.join(ASSETS, "jfk.wav"), "-shortest", "-c:v", "libx264",
              "-pix_fmt", "yuv420p", "-c:a", "aac", video])
    ui.reset()
    tab = ui.open("Auto-Subtitles")
    tab.src_var.set(video)
    ui.pump(0.6)
    tab._fill_models("base")
    tab._run()
    ui.wait_idle(300)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    srt = str(outdir / "jfk talk.srt")
    text = open(srt, encoding="utf-8").read()
    assert "-->" in text and "country" in text.lower()
    # soft subtitle track
    tab._mux()
    ui.wait_idle(120)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    muxed = str(outdir / "jfk talk_subtitled.mp4")
    info = ff.probe(muxed, use_cache=False)
    subs = [s for s in info.streams if s.type == "subtitle"]
    assert subs and subs[0].codec == "mov_text" and subs[0].language == "eng"
    # hand-off to the Hard-Subber
    tab._burn()
    hs = ui.app.page(ToolId.HARD_SUBBER)
    assert hs is not None and hs.sub_row.path == srt


@needs_assets
@pytest.mark.gui
@pytest.mark.ffmpeg
def test_karaoke_from_audio(ui, outdir):
    shutil.copyfile(os.path.join(ASSETS, "ggml-base-q5_1.bin"), whisper.model_path("base"))
    with open(whisper.model_path("base") + ".ok", "w") as fh:
        fh.write("test\n")
    ui.reset()
    tab = ui.open("Karaoke Generator")
    tab._src_var.set(os.path.join(ASSETS, "jfk.wav"))
    tab._model_var.set(next(k for k, v in __import__("tabs.audio.karaoke", fromlist=["MODELS"]).MODELS.items()
                            if v == "base"))
    out = str(outdir / "karaoke.mp4")
    tab._out_var.set(out)
    tab._generate()
    ui.wait_idle(300)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    info = ff.probe(out, use_cache=False)
    assert info.has_video and info.require_video().pix_fmt == "yuv420p" and info.duration > 10


def test_karaoke_word_grouping(tmp_path):
    from tabs.audio.karaoke import segments_from_whispercpp
    p = tmp_path / "w.json"
    p.write_text('{"transcription": [{"offsets": {"from": 0, "to": 2000}, "text": " Hello world", "tokens": ['
                 '{"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}},'
                 '{"text": " Hel", "offsets": {"from": 100, "to": 300}},'
                 '{"text": "lo", "offsets": {"from": 300, "to": 500}},'
                 '{"text": " world", "offsets": {"from": 600, "to": 1200}}]}]}')
    segs = segments_from_whispercpp(str(p))
    assert [w["word"] for w in segs[0]["words"]] == [" Hello", " world"]
    assert segs[0]["words"][0]["end"] == 0.5
