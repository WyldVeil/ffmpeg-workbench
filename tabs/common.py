"""
tabs/visuals/common.py - shared plumbing for the single-source visual tools
(colour grading, LUTs, keys, stabilisation, speed...).

Every tool follows the same shape: validate the source, build a filter chain,
pick an encoder from the EncoderPanel, keep the audio when the container can
hold it, and queue one render with progress, cancel and overwrite protection.
"""
from __future__ import annotations

import os
import time
import tkinter as tk
from collections.abc import Sequence
from functools import partial
from tkinter import messagebox

from core import encoders as enc
from core import ffmpeg as ff
from core import tasks
from core.i18n import t


def container_of(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".") or "mp4"


def keep_audio_args(info: ff.MediaInfo | None, container: str, *, index: int = 0,
                    filters: str = "") -> list:
    """Map all audio of input *index*: stream-copied when the container accepts
    the codec and no *filters* are applied, otherwise re-encoded."""
    if info is None or not info.has_audio:
        return ["-an"]
    args = ["-map", f"{index}:a?"]
    if filters:
        return args + ["-af", filters] + enc.audio(enc.default_audio_for(container),
                                                   container=container)
    key = enc.audio_key_from_probe(info.audio.codec)
    if key and enc.audio_ok_for(container, key):
        return args + ["-c:a", "copy"]
    return args + enc.audio(enc.default_audio_for(container), container=container)


def check_source(tab, path: str, *, need_video: bool = True,
                 what: str = "") -> ff.MediaInfo | None:
    """Probe *path*; explain the problem and return None when unusable."""
    label = what or t("common.source_video")
    if not path:
        messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=tab)
        return None
    if not os.path.isfile(path):
        messagebox.showwarning(t("common.warning"), f"{label}\n\nFile not found:\n{path}",
                               parent=tab)
        return None
    info = ff.try_probe(path)
    if info is None or (need_video and not info.has_video):
        messagebox.showerror(t("common.error"),
                             f"{label}\n\nThis file has no readable video:\n{path}", parent=tab)
        return None
    return info


def number(value, label: str, lo: float | None = None, hi: float | None = None) -> float:
    """Parse a user number ('1,5' accepted); ValueError names the field."""
    try:
        val = float(str(value).strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"'{label}' must be a number.") from None
    if (lo is not None and val < lo) or (hi is not None and val > hi):
        raise ValueError(f"'{label}' must be between {lo:g} and {hi:g}.")
    return val


def even_size(info: ff.MediaInfo) -> tuple[int, int]:
    w, h = info.video.display_size
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


def frame_rate(info: ff.MediaInfo) -> str:
    """Exact source frame rate as a rational string (e.g. 30000/1001)."""
    v = info.video
    if v is None or not (0 < (v.fps or 0) <= 240):
        return "30"
    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = v.raw.get(key) or ""
        if rate and not rate.startswith("0/") and rate != "0":
            return rate
    return f"{v.fps:g}"


def render(tab, *, src: str, info: ff.MediaInfo, out: str, encoder, console, btn,
           btn_label: str, name: str, chain: str = "", graph: str = "",
           extra_inputs: Sequence[str] = (), audio: Sequence[str] | None = None,
           duration: float | None = None, extra_args: Sequence[str] = (),
           inputs: Sequence[str] = (), on_done=None, pre_input: Sequence[str] = ()) -> None:
    """Queue ``ffmpeg -i src [extra inputs] <chain|graph> <audio> <encoder> out``.

    *chain* is a simple ``-vf`` chain; *graph* a ``-filter_complex`` graph
    whose video output is labelled ``[v]``.  *encoder* is an EncoderPanel or
    a ready :class:`core.encoders.VideoEncode`."""
    container = container_of(out)
    venc = encoder.video_encode(container) if hasattr(encoder, "video_encode") else encoder
    cmd = [ff.ffmpeg_path(), "-y", *pre_input, *venc.pre_input, "-i", src, *extra_inputs]
    if graph:
        if venc.filter_suffix:
            graph = f"{graph};[v]{venc.filter_suffix}[venc]"
            cmd += ["-filter_complex", graph, "-map", "[venc]"]
        else:
            cmd += ["-filter_complex", graph, "-map", "[v]"]
    else:
        cmd += ["-map", "0:v:0"]
        cmd += venc.vf_args(chain)
    cmd += list(audio if audio is not None else keep_audio_args(info, container))
    cmd += [*extra_args, *venc.args, "-map_metadata", "0", out]
    tab.log(console, f"Rendering {os.path.basename(out)} with {venc.label}…")

    def _done(rc):
        if on_done is not None:
            on_done(rc)
        else:
            tab.show_result(rc, out)

    tab.run_ffmpeg(cmd, console, on_done=_done, btn=btn, btn_label=btn_label,
                   duration=duration or info.duration or None, output=out,
                   inputs=[src, *inputs], name=name)


def output_for(tab, out_row, src: str, suffix: str, inputs: Sequence[str] = ()) -> str | None:
    """Final output path from *out_row* (suggested when empty), confirmed
    against overwriting; None if the user backed out."""
    out = out_row.path or tab.suggest_output(src, suffix, ".mp4")
    out = tab.confirm_output(out, [src, *inputs])
    if out:
        out_row.set(out)
    return out


def queue_job(tab, *, name: str, out: str, work, btn, btn_label: str, console,
              duration: float | None = None, inputs: Sequence[str] = (),
              on_start=None, on_done=None) -> str:
    """Queue a multi-step ``work(progress, cancelled, ctx)`` job.  While it
    runs, *btn* shows the progress and cancels the job when clicked;
    ``on_done(rc)`` runs on the UI thread afterwards."""
    orig = btn.cget("command")
    queued_at = time.monotonic()

    def cancel():
        if time.monotonic() - queued_at > 0.8:      # ignore a double-click on Render
            tab.cancel_render(state["tid"])

    def on_progress(tid, line):
        tab.log(console, line)
        task = tab.task(tid)
        if task is not None and task.fraction is not None:
            try:
                btn.config(text=f"\u2716 {t('btn.cancel')}  {task.fraction * 100:.0f}%")
            except tk.TclError:
                pass

    def on_complete(tid, rc):
        try:
            btn.config(text=btn_label, command=orig, state="normal")
        except tk.TclError:
            pass
        tab._last_task = tab.task(tid)
        tab.show_result(rc, out)
        if on_done is not None:
            on_done(rc)

    state = {"tid": None}
    state["tid"] = tab.enqueue_render(name, out, worker_fn=work, on_start=on_start,
                                      on_progress=on_progress, on_complete=on_complete,
                                      expected_duration=duration, inputs=inputs)
    btn.config(text=f"\u2716 {t('btn.cancel')}", command=cancel)
    tab.log(console, f"Queued: {os.path.basename(out)}")
    return state["tid"]


def intermediate_args(venc) -> list:
    """Encoder args for temporary pieces that are joined later: drops the
    container-specific flags (faststart, hvc1 tag) that only belong on the
    final file - see :func:`final_copy_args`."""
    out, skip = [], False
    for a in venc.args:
        if skip:
            skip = False
            continue
        if a in ("-movflags", "-tag:v"):
            skip = True
            continue
        out.append(a)
    return out


def final_copy_args(venc, container: str) -> list:
    """Flags for the final stream-copied file made from intermediate pieces."""
    args = []
    if container in ("mp4", "mov", "m4v"):
        args += ["-movflags", "+faststart"]
        if venc.codec == "hevc":
            args += ["-tag:v", "hvc1"]
    return args


# ── Audio tools ──────────────────────────────────────────────────────────────

AUDIO_ONLY_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".mka", ".aiff", ".aif")


def audio_encoder_args(out: str) -> list:
    """Encoder for processed audio, chosen from the output file's type."""
    ext = os.path.splitext(out)[1].lower()
    return {
        ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        ".wav": ["-c:a", "pcm_s16le"],
        ".aiff": ["-c:a", "pcm_s16be"], ".aif": ["-c:a", "pcm_s16be"],
        ".flac": ["-c:a", "flac", "-compression_level", "8"],
        ".m4a": ["-c:a", "aac", "-b:a", "256k"], ".aac": ["-c:a", "aac", "-b:a", "256k"],
        ".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
        ".opus": ["-c:a", "libopus", "-b:a", "160k"],
        ".mka": ["-c:a", "flac"],
        ".webm": ["-c:a", "libopus", "-b:a", "160k"],
    }.get(ext, ["-c:a", "aac", "-b:a", "256k"])


def audio_job_args(out: str, info: ff.MediaInfo | None, *, stream: int = 0) -> list:
    """Mapping + codecs when an audio tool writes *out*: audio-only files get
    just the processed track; video files keep the picture (copied) and
    subtitles where the container allows."""
    ext = os.path.splitext(out)[1].lower()
    if ext in AUDIO_ONLY_EXTS or info is None or not info.has_video:
        return ["-vn", "-sn", "-map", f"0:a:{stream}", *audio_encoder_args(out)]
    args = ["-map", "0:v?", "-c:v", "copy", "-map", f"0:a:{stream}", *audio_encoder_args(out)]
    if ext == ".mkv":
        args += ["-map", "0:s?", "-c:s", "copy"]
    if ext in (".mp4", ".mov", ".m4v"):
        args += ["-movflags", "+faststart"]
    return args


def preview_audio(tab, src: str, af: str, start: float = 0.0, seconds: float = 12.0) -> None:
    """Render a short processed WAV in the background and play it."""
    from core.hardware import launch_preview
    from core.paths import temp_dir
    if not src or not os.path.isfile(src):
        messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=tab)
        return
    wav = os.path.join(temp_dir("preview_"), "preview.wav")
    cmd = [ff.ffmpeg_path(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
           "-ss", ff.ts(start), "-t", ff.ts(seconds), "-i", src, "-map", "0:a:0", "-vn",
           *(["-af", af] if af else []), "-c:a", "pcm_s16le", wav]

    def done(res):
        if res.returncode != 0 or not os.path.isfile(wav):
            messagebox.showerror(t("common.error"), "Preview failed:\n" + ff.summarize_error(
                (res.stderr or "").splitlines(), res.returncode), parent=tab)
            return
        if launch_preview(wav) is None:
            messagebox.showinfo(t("common.warning"), f"No player available - the preview is at\n{wav}",
                                parent=tab)

    tasks.submit(partial(ff.capture, cmd, timeout=120), on_done=done, name="AudioPreview")
