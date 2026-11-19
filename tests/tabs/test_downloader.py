"""Video Downloader: cancel must kill only the download, never the app."""
import os
import sys
import threading
import time

import pytest

from core import ytdlp


def test_base_argv_has_runtime_and_ffmpeg():
    argv = ytdlp.base_argv("/x/yt-dlp", network=False)
    assert argv[:2] == ["/x/yt-dlp", "--ignore-config"]
    assert "--ffmpeg-location" in argv
    red = ytdlp.redact(["yt-dlp", "--password", "hunter2", "--proxy", "socks5://u:p@h:1", "URL"])
    assert "hunter2" not in red and "socks5://u:p@h:1" not in red


@pytest.mark.gui
def test_cancel_kills_child_not_app(ui):
    tab = ui.open("Video Downloader")
    script = "import time\nwhile True: time.sleep(0.1)"
    done = []
    t = threading.Thread(target=tab._stream_ytdlp,
                         args=([sys.executable, "-c", script],),
                         kwargs={"on_done": done.append}, daemon=True)
    t.start()
    ui.wait_for(lambda: tab._active_proc is not None, 10)
    child = tab._active_proc
    tab._cancel_download()
    t.join(10)
    assert child.poll() is not None              # the download is gone
    assert os.getpid()                           # ...and we are still alive
    ui.pump(0.3)
    assert done and done[0] != 0
