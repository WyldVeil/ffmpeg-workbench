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
from tkinter import messagebox
from typing import Optional, Sequence

from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t


def container_of(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".") or "mp4"


def keep_audio_args(info: Optional[ff.MediaInfo], container: str, *, index: int = 0,
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
                 what: str = "") -> Optional[ff.MediaInfo]:
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


def number(value, label: str, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
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
           extra_inputs: Sequence[str] = (), audio: Optional[Sequence[str]] = None,
           duration: Optional[float] = None, extra_args: Sequence[str] = (),
           inputs: Sequence[str] = (), on_done=None) -> None:
    """Queue ``ffmpeg -i src [extra inputs] <chain|graph> <audio> <encoder> out``.

    *chain* is a simple ``-vf`` chain; *graph* a ``-filter_complex`` graph
    whose video output is labelled ``[v]``.  *encoder* is an EncoderPanel or
    a ready :class:`core.encoders.VideoEncode`."""
    container = container_of(out)
    venc = encoder.video_encode(container) if hasattr(encoder, "video_encode") else encoder
    cmd = [ff.ffmpeg_path(), "-y", *venc.pre_input, "-i", src, *extra_inputs]
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


def output_for(tab, out_row, src: str, suffix: str, inputs: Sequence[str] = ()) -> Optional[str]:
    """Final output path from *out_row* (suggested when empty), confirmed
    against overwriting; None if the user backed out."""
    out = out_row.path or tab.suggest_output(src, suffix, ".mp4")
    out = tab.confirm_output(out, [src, *inputs])
    if out:
        out_row.set(out)
    return out


def queue_job(tab, *, name: str, out: str, work, btn, btn_label: str, console,
              duration: Optional[float] = None, inputs: Sequence[str] = (),
              on_start=None) -> str:
    """Queue a multi-step ``work(progress, cancelled, ctx)`` job.  While it
    runs, *btn* shows the progress and cancels the job when clicked."""
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

    state = {"tid": None}
    state["tid"] = tab.enqueue_render(name, out, worker_fn=work, on_start=on_start,
                                      on_progress=on_progress, on_complete=on_complete,
                                      expected_duration=duration, inputs=inputs)
    btn.config(text=f"\u2716 {t('btn.cancel')}", command=cancel)
    tab.log(console, f"Queued: {os.path.basename(out)}")
    return state["tid"]
