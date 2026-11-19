"""System tools: Screen Recorder back end, settings pages, System Info privacy."""

import os
import shutil
import subprocess
import sys
import time

import pytest

from core import ffmpeg as ff


def test_recording_names_never_overwrite(tmp_path):
    from core import recorder
    a = recorder.recording_name(str(tmp_path), "Clip {date}", ".mp4")
    open(a, "w").close()
    b = recorder.recording_name(str(tmp_path), "Clip {date}", ".mp4")
    assert a != b and b.endswith(" (2).mp4")


def test_ffmpeg_command_layout():
    from core import recorder
    cmd = recorder.ffmpeg_command(["-f", "lavfi", "-i", "testsrc"], ["mon.monitor", "default"], "/x/o.mp4",
                                  ["-c:v", "libx264"], seconds=5)
    joined = " ".join(cmd)
    assert "amix=inputs=2:duration=longest:normalize=0" in joined
    assert cmd.index("-t") > cmd.index("-c:v")                    # output option, after all inputs
    assert "+frag_keyframe" in joined                              # crash-safe MP4


@pytest.mark.ffmpeg
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal path")
def test_recording_stops_cleanly_and_is_playable(tmp_path):
    from core import recorder
    out = str(tmp_path / "rec.mp4")
    cmd = recorder.ffmpeg_command(["-re", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30"], [], out,
                                  ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
    ended = {}
    rec = recorder.Recording(cmd, out, on_exit=lambda r, rc: ended.setdefault("rc", rc))
    time.sleep(2.0)
    rec.stop()
    time.sleep(0.3)
    assert rec.stopping and "rc" in ended
    info = ff.probe(out, use_cache=False)
    assert info.has_video and info.duration > 1.0


@pytest.mark.ffmpeg
def test_recording_failure_is_noticed(tmp_path):
    from core import recorder
    out = str(tmp_path / "bad.mp4")
    cmd = recorder.ffmpeg_command(["-f", "lavfi", "-i", "nosuchsource"], [], out, ["-c:v", "libx264"])
    ended = {}
    rec = recorder.Recording(cmd, out, on_exit=lambda r, rc: ended.setdefault("rc", rc))
    for _ in range(50):
        if "rc" in ended:
            break
        time.sleep(0.1)
    assert ended.get("rc") not in (None, 0) and rec.error_text()


@pytest.mark.gui
def test_settings_pages_do_not_revert_other_changes(ui):
    from core import settings
    ui.reset()
    page = ui.open("Advanced Settings")
    settings.update({"favourites": ["Audio Mixer"], "test_marker": "kept"})
    page._save_settings()
    assert settings.load_settings().get("test_marker") == "kept"
    assert settings.load_settings().get("favourites") == ["Audio Mixer"]


@pytest.mark.gui
def test_system_info_has_no_addresses(ui, monkeypatch):
    import tkinter as tk
    import urllib.request
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError()))
    ui.reset()
    page = ui.open("Advanced Settings")
    area = tk.Text(page)
    page._load_sysinfo(area)
    ui.pump(1.0)
    text = area.get("1.0", "end")
    assert "PYTHON" in text.upper()
    assert not calls and "Public IP" not in text and "LAN IP" not in text
    assert "Hostname" not in text


@pytest.fixture(scope="module")
def ui():
    from tests.gui_harness import Harness
    return Harness.instance()


@pytest.mark.gui
@pytest.mark.ffmpeg
@pytest.mark.parametrize("op", ["to_mp4", "loudness", "strip_meta", "resize", "audio_mp3"])
def test_batch_operations(ui, media, tmp_path, op):
    from tabs.system.batch import OPERATION_LIST
    a = tmp_path / "a" / "clip.mkv"
    b = tmp_path / "b" / "clip.mkv"
    for f in (a, b):
        f.parent.mkdir()
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-i", media["h264_1080p25"], "-c", "copy",
                    "-metadata", "title=secret", "-metadata:s:a:0", "language=eng", str(a)], check=True)
    shutil.copyfile(a, b)
    out = tmp_path / "out"
    ui.reset()
    tab = ui.open("Batch Processor")
    tab._clear()
    tab.load_file(str(a))
    tab.load_file(str(b))
    tab._op_var.set(dict(OPERATION_LIST)[op])
    tab._res_var.set("1280x720")
    tab._out_var.set(str(out))
    tab._run()
    ui.wait_idle(300)
    assert not ui.dialogs.errors(), ui.dialogs.errors()
    made = sorted(os.listdir(out))
    assert len(made) == 2 and len(set(made)) == 2, made           # same names never overwrite
    first = ff.probe(str(out / made[0]), use_cache=False)
    if op == "strip_meta":
        assert "title" not in first.tags and first.audio.tags.get("language") == "eng"
    if op == "resize":
        assert first.video.height == 720
    if op == "to_mp4":
        assert first.video.codec == "h264" and made[0].endswith(".mp4")


@pytest.mark.gui
def test_network_page_situations(ui):
    from core import network as net
    ui.reset()
    page = ui.open("Advanced Settings")
    clash = next(t for t, sid in page._net_app_labels.items() if sid == "clash")
    page._net_app_var.set(clash)
    page._net_situation_var.set("app")
    page._net_choose("app")
    page._net_vars["network.tor_retries"].set("5")
    page._net_save()
    c = net.get()
    assert (net.mode(c), c["network.proxy_port"], c["network.proxy_scheme"]) == ("manual", "7890", "http")
    assert net.tor_retries(c) == 5
    page._net_situation_var.set("auto")
    page._net_choose("auto")
    page._net_save()
    assert net.mode() == "auto" and not net.strict()
