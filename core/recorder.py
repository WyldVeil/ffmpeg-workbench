"""
core/recorder.py  -  Screen-recording back end

* Capture input for Windows (gdigrab), macOS (avfoundation) and X11
  (x11grab on the session's real $DISPLAY).
* Wayland can't be captured by FFmpeg itself: gpu-screen-recorder or
  wf-recorder are used when installed (both go through the desktop's own
  screen-sharing portal / protocol); without them only X11 (XWayland)
  windows are visible, and the caller is told so.
* Audio sources: dshow devices (FFmpeg 5+ listing format), avfoundation, or
  PulseAudio / PipeWire sources - including the "monitor" of the default
  output, i.e. the computer's own sound.
* :class:`Recording` runs the recorder, keeps a log tail, notices when it
  exits on its own, and stops it gracefully (FFmpeg 'q' / SIGINT) so the
  file is finalised; MP4 is written fragmented, so even a crash leaves a
  playable file.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import sys
import time
from collections import deque
from datetime import datetime
from functools import partial

from core import ffmpeg as ff
from core import proc as _proc
from core import tasks

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def session() -> str:
    """'windows', 'mac', 'wayland' or 'x11'."""
    if IS_WIN:
        return "windows"
    if IS_MAC:
        return "mac"
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    return "x11"


def wayland_tool() -> str | None:
    """A screen recorder that works on Wayland, if one is installed."""
    for name in ("gpu-screen-recorder", "wf-recorder"):
        path = shutil.which(name)
        if path:
            return path
    return None


def x11_display() -> str:
    return os.environ.get("DISPLAY") or ":0"


def recording_name(folder: str, template: str = "Recording {date} {time}", ext: str = ".mp4") -> str:
    """Timestamped file name that never replaces an existing file."""
    now = datetime.now()
    name = (template or "Recording {date} {time}").replace("{date}", now.strftime("%Y-%m-%d")) \
        .replace("{time}", now.strftime("%H-%M-%S")).replace("{counter}", "")
    name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "Recording"
    base = os.path.join(folder, name)
    path, n = base + ext, 2
    while os.path.exists(path):
        path = f"{base} ({n}){ext}"
        n += 1
    return path


def default_folder() -> str:
    for cand in (os.path.join(os.path.expanduser("~"), "Videos"),
                 os.path.join(os.path.expanduser("~"), "Movies"), os.path.expanduser("~")):
        if os.path.isdir(cand):
            return cand
    return os.path.expanduser("~")


# ── Audio sources ─────────────────────────────────────────────────────────────

def audio_sources() -> list:
    """[(id, label)] - id is what the capture input needs."""
    out = []
    try:
        if IS_WIN:
            r = ff.capture([ff.ffmpeg_path(), "-hide_banner", "-list_devices", "true", "-f", "dshow",
                            "-i", "dummy"], timeout=15)
            for line in (r.stderr or "").splitlines():
                m = re.search(r'"([^"]+)"\s*\(audio\)', line)          # FFmpeg 5+ format
                if m and m.group(1) not in [x[0] for x in out]:
                    out.append((m.group(1), m.group(1)))
        elif IS_MAC:
            r = ff.capture([ff.ffmpeg_path(), "-hide_banner", "-f", "avfoundation", "-list_devices", "true",
                            "-i", ""], timeout=15)
            audio = False
            for line in (r.stderr or "").splitlines():
                if "audio devices" in line.lower():
                    audio = True
                    continue
                m = re.search(r"\[(\d+)\]\s+(.+)$", line)
                if audio and m:
                    out.append((f":{m.group(1)}", m.group(2).strip()))
        else:
            pactl = shutil.which("pactl")
            if pactl:
                sink = _proc.run([pactl, "get-default-sink"], timeout=5).stdout.strip()
                if sink:
                    out.append((sink + ".monitor", "Computer sound (what you hear)"))
                out.append(("default", "Default microphone"))
                r = _proc.run([pactl, "list", "short", "sources"], timeout=5)
                for line in r.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) > 1 and parts[1] not in [x[0] for x in out]:
                        out.append((parts[1], parts[1]))
            else:
                out.append(("default", "Default microphone"))
    except (OSError, _proc.ProcessError):
        pass
    return out


def system_sound_source(sources: list) -> str | None:
    """Best guess at 'the computer's own sound' among *sources*."""
    for sid, label in sources:
        low = (sid + " " + label).lower()
        if sid.endswith(".monitor") or any(k in low for k in ("stereo mix", "what u hear", "loopback",
                                                            "wave out", "mezcla", "stereomix",
                                                            "virtual-audio-capturer")):
            return sid
    return None


def audio_input(source: str) -> list:
    if IS_WIN:
        return ["-thread_queue_size", "1024", "-f", "dshow", "-i", f"audio={source}"]
    if IS_MAC:
        return ["-f", "avfoundation", "-i", source if source.startswith(":") else f":{source}"]
    return ["-thread_queue_size", "1024", "-f", "pulse", "-i", source]


# ── Capture commands ──────────────────────────────────────────────────────────

def screen_input(fps: str = "30", *, region: tuple | None = None, cursor: bool = True,
                 window_title: str = "") -> list:
    """FFmpeg input arguments for the screen (Windows / macOS / X11)."""
    if IS_WIN:
        args = ["-thread_queue_size", "1024", "-f", "gdigrab", "-framerate", str(fps)]
        if not cursor:
            args += ["-draw_mouse", "0"]
        if window_title:
            return [*args, "-i", f"title={window_title}"]
        if region:
            x, y, w, h = region
            args += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w // 2 * 2}x{h // 2 * 2}"]
        return [*args, "-i", "desktop"]
    if IS_MAC:
        return ["-f", "avfoundation", "-capture_cursor", "1" if cursor else "0", "-framerate", str(fps),
                "-i", "1:none"]
    args = ["-thread_queue_size", "1024", "-f", "x11grab", "-framerate", str(fps),
            "-draw_mouse", "1" if cursor else "0"]
    if region:
        x, y, w, h = region
        return [*args, "-video_size", f"{w // 2 * 2}x{h // 2 * 2}", "-i", f"{x11_display()}+{x},{y}"]
    return [*args, "-i", x11_display()]


def ffmpeg_command(video_in: list, audio_sources_: list, out: str, video_args: list, *,
                   audio_codec: str = "aac", region_crop: tuple | None = None,
                   seconds: int | None = None, pre_input: list | None = None,
                   vf_suffix: str = "") -> list:
    cmd = [ff.ffmpeg_path(), "-hide_banner", "-y", *(pre_input or []), *video_in]
    for src in audio_sources_:
        cmd += audio_input(src)
    vf = "crop=trunc(iw/2)*2:trunc(ih/2)*2"                   # encoders need even sizes
    if region_crop and IS_MAC:
        x, y, w, h = region_crop
        vf = f"crop={w // 2 * 2}:{h // 2 * 2}:{x}:{y}"
    if vf_suffix:                                            # e.g. VAAPI upload
        vf += "," + vf_suffix
    n = len(audio_sources_)
    if n >= 2:
        mix = "".join(f"[{i + 1}:a]" for i in range(n))
        cmd += ["-filter_complex", f"[0:v]{vf}[v];{mix}amix=inputs={n}:duration=longest:normalize=0[a]",
                "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-vf", vf]
        if n == 1:
            cmd += ["-map", "1:a"]
    cmd += video_args
    if n:
        ext = os.path.splitext(out)[1].lower()
        codec = audio_codec
        if ext == ".mp4" and codec in ("pcm_s16le",):
            codec = "aac"
        if ext == ".mov" and codec in ("flac", "libopus"):
            codec = "aac"
        cmd += ["-c:a", codec] + (["-b:a", "192k"] if codec in ("aac", "libopus") else [])
    if seconds:
        cmd += ["-t", str(int(seconds))]                    # output option: stops every input
    if out.lower().endswith((".mp4", ".mov")):
        cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"]   # playable after a crash
    return [*cmd, out]


def wayland_command(tool: str, out: str, *, fps: str = "60", audio: tuple = ("output",),
                    seconds: int | None = None) -> list:
    """Command for gpu-screen-recorder / wf-recorder (audio: 'output' and/or 'input')."""
    name = os.path.basename(tool)
    if name.startswith("gpu-screen-recorder"):
        cmd = [tool, "-w", "portal", "-f", str(fps), "-o", out]
        for a in audio:
            cmd += ["-a", "default_output" if a == "output" else "default_input"]
        return cmd
    cmd = [tool, "-f", out, "-r", str(fps)]
    if "output" in audio:
        sink = ""
        with contextlib.suppress(OSError, _proc.ProcessError):
            sink = _proc.run(["pactl", "get-default-sink"], timeout=5).stdout.strip()
        cmd += [f"--audio={sink}.monitor"] if sink else ["--audio"]
    return cmd


class Recording:
    """One running recorder process."""

    def __init__(self, cmd: list, out: str, *, on_exit=None, seconds: int | None = None):
        self.cmd, self.out, self.on_exit = cmd, out, on_exit
        self.tail = deque(maxlen=60)
        self.started = time.monotonic()
        self.stopping = False
        self.seconds = seconds
        self.is_ffmpeg = os.path.basename(cmd[0]).lower().startswith("ffmpeg")
        self.proc = _proc.spawn(cmd, stdin=_proc.PIPE, stdout=_proc.PIPE, stderr=_proc.STDOUT, text=True)
        tasks.start_service(self._pump, name="RecorderLog")
        if seconds and not self.is_ffmpeg:
            tasks.start_service(partial(self._stop_after, seconds), name="RecorderTimer")

    def _pump(self):
        out = self.proc.stdout
        assert out is not None, "spawned with stdout=PIPE"
        for line in iter(out.readline, ""):
            line = line.rstrip()
            if line:
                self.tail.append(line)
        rc = self.proc.wait()
        if self.on_exit is not None:
            self.on_exit(self, rc)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def running(self) -> bool:
        return self.proc.poll() is None

    def _stop_after(self, seconds: float) -> None:
        """Timed recordings by tools without a duration option: stop at the time."""
        try:
            self.proc.wait(timeout=seconds)
        except _proc.TimeoutExpired:
            self.stop()

    def stop(self, timeout: float = 10.0) -> int:
        """Finish the file properly; kill only if the recorder doesn't react."""
        self.stopping = True
        if self.proc.poll() is None:
            try:
                if self.is_ffmpeg and self.proc.stdin is not None:
                    self.proc.stdin.write("q\n")
                    self.proc.stdin.flush()
                elif IS_WIN:
                    self.proc.terminate()
                else:
                    self.proc.send_signal(signal.SIGINT)
            except (OSError, ValueError):
                pass
            try:
                self.proc.wait(timeout=timeout)
            except _proc.ProcessTimeout:
                self.proc.kill()
                self.proc.wait()
        return self.proc.returncode

    def error_text(self) -> str:
        lines = [x for x in self.tail if not x.startswith(("frame=", "size="))]
        return "\n".join(lines[-8:])
