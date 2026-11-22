"""
tabs/visuals/common.py - shared plumbing for the single-source visual tools
(colour grading, LUTs, keys, stabilisation, speed...).

Every tool follows the same shape: validate the source, build a filter chain,
pick an encoder from the EncoderPanel, keep the audio when the container can
hold it, and queue one render with progress, cancel and overwrite protection.
"""

from __future__ import annotations

import contextlib
import os
import time
import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from tkinter import messagebox
from typing import Any

from core import ffmpeg as ff
from core import tasks
from core.encoders import VideoEncode
from core.i18n import t
from core.ops.shared import keep_audio_args
from core.queue_manager import Hooks, WorkerFn


def container_of(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".") or "mp4"


def check_source(tab, path: str, *, need_video: bool = True, what: str = "") -> ff.MediaInfo | None:
    """Probe *path*; explain the problem and return None when unusable."""
    label = what or t("common.source_video")
    if not path:
        messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=tab)
        return None
    if not os.path.isfile(path):
        messagebox.showwarning(
            t("common.warning"), f"{label}\n\nFile not found:\n{path}", parent=tab
        )
        return None
    info = ff.try_probe(path)
    if info is None or (need_video and not info.has_video):
        messagebox.showerror(
            t("common.error"), f"{label}\n\nThis file has no readable video:\n{path}", parent=tab
        )
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
    w, h = info.require_video().display_size
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderJob:
    """One render: ``ffmpeg [pre_input] -i src [extra_inputs] <chain|graph> <audio>
    <encoder> [extra_args] out``.

    *chain* is a simple ``-vf`` chain; *graph* a ``-filter_complex`` graph whose
    video output is labelled ``[v]``.  *audio* defaults to keeping the source's
    audio; *inputs* are further files the job reads (guarded against overwriting)."""

    src: str
    info: ff.MediaInfo
    out: str
    name: str
    chain: str = ""
    graph: str = ""
    extra_inputs: Sequence[str] = ()
    audio: Sequence[str] | None = None
    duration: float | None = None
    extra_args: Sequence[str] = ()
    inputs: Sequence[str] = ()
    pre_input: Sequence[str] = ()

    def command(self, venc: VideoEncode) -> list[str]:
        container = container_of(self.out)
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            *self.pre_input,
            *venc.pre_input,
            "-i",
            self.src,
            *self.extra_inputs,
        ]
        if self.graph:
            if venc.filter_suffix:
                cmd += [
                    "-filter_complex",
                    f"{self.graph};[v]{venc.filter_suffix}[venc]",
                    "-map",
                    "[venc]",
                ]
            else:
                cmd += ["-filter_complex", self.graph, "-map", "[v]"]
        else:
            cmd += ["-map", "0:v:0", *venc.vf_args(self.chain)]
        cmd += list(self.audio if self.audio is not None else keep_audio_args(self.info, container))
        return [*cmd, *self.extra_args, *venc.args, "-map_metadata", "0", self.out]


def render(
    tab: Any,
    job: RenderJob,
    *,
    encoder: Any,
    console: tk.Text,
    btn: tk.Button,
    on_done: Callable[[int], object] | None = None,
) -> None:
    """Queue *job*.  *encoder* is an EncoderPanel or a ready
    :class:`core.encoders.VideoEncode`; afterwards *on_done(rc)* runs, or the
    result is shown."""
    container = container_of(job.out)
    venc = encoder.video_encode(container) if hasattr(encoder, "video_encode") else encoder
    tab.log(console, f"Rendering {os.path.basename(job.out)} with {venc.label}…")

    def _done(rc: int) -> None:
        if on_done is not None:
            on_done(rc)
        else:
            tab.show_result(rc, job.out)

    tab.run_ffmpeg(
        job.command(venc),
        console,
        on_done=_done,
        btn=btn,
        duration=job.duration or job.info.duration or None,
        output=job.out,
        inputs=[job.src, *job.inputs],
        name=job.name,
    )


def output_for(tab, out_row, src: str, suffix: str, inputs: Sequence[str] = ()) -> str | None:
    """Final output path from *out_row* (suggested when empty), confirmed
    against overwriting; None if the user backed out."""
    out = out_row.path or tab.suggest_output(src, suffix, ".mp4")
    out = tab.confirm_output(out, [src, *inputs])
    if out:
        out_row.set(out)
    return out


@dataclass(frozen=True, slots=True)
class Job:
    """A multi-step job for :func:`queue_job`: *work(progress, cancelled, ctx)*
    writes *out*, reading *inputs*; *duration* is its expected length."""

    name: str
    out: str
    work: WorkerFn
    duration: float | None = None
    inputs: Sequence[str] = ()


def queue_job(
    tab: Any, job: Job, *, btn: tk.Button, console: tk.Text, hooks: Hooks | None = None
) -> str:
    """Queue *job*.  While it runs, *btn* shows the progress and cancels the
    job when clicked; afterwards the result is shown, then *hooks.on_complete*
    runs (on the UI thread, like *hooks.on_start*)."""
    hooks = hooks or Hooks()
    orig, label = btn.cget("command"), btn.cget("text")
    queued_at = time.monotonic()

    def cancel() -> None:
        if time.monotonic() - queued_at > 0.8:  # ignore a double-click on Render
            tab.cancel_render(state["tid"])

    def on_progress(tid: str, line: str) -> None:
        tab.log(console, line)
        task = tab.task(tid)
        if task is not None and task.fraction is not None:
            with contextlib.suppress(tk.TclError):
                btn.config(text=f"\u2716 {t('btn.cancel')}  {task.fraction * 100:.0f}%")

    def on_complete(tid: str, rc: int) -> None:
        with contextlib.suppress(tk.TclError):
            btn.config(text=label, command=orig, state="normal")
        tab.last_task = tab.task(tid)
        tab.show_result(rc, job.out)
        if hooks.on_complete is not None:
            hooks.on_complete(tid, rc)

    state = {"tid": None}
    state["tid"] = tab.enqueue_render(
        job.name,
        job.out,
        work=job.work,
        expected_duration=job.duration,
        inputs=job.inputs,
        hooks=Hooks(on_start=hooks.on_start, on_progress=on_progress, on_complete=on_complete),
    )
    btn.config(text=f"\u2716 {t('btn.cancel')}", command=cancel)
    tab.log(console, f"Queued: {os.path.basename(job.out)}")
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


def preview_audio(tab, src: str, af: str, start: float = 0.0, seconds: float = 12.0) -> None:
    """Render a short processed WAV in the background and play it."""
    from core.hardware import launch_preview
    from core.paths import temp_dir

    if not src or not os.path.isfile(src):
        messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=tab)
        return
    wav = os.path.join(temp_dir("preview_"), "preview.wav")
    cmd = [
        ff.ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        ff.ts(start),
        "-t",
        ff.ts(seconds),
        "-i",
        src,
        "-map",
        "0:a:0",
        "-vn",
        *(["-af", af] if af else []),
        "-c:a",
        "pcm_s16le",
        wav,
    ]

    def done(res):
        if res.returncode != 0 or not os.path.isfile(wav):
            messagebox.showerror(
                t("common.error"),
                "Preview failed:\n"
                + ff.summarize_error((res.stderr or "").splitlines(), res.returncode),
                parent=tab,
            )
            return
        if launch_preview(wav) is None:
            messagebox.showinfo(
                t("common.warning"), f"No player available - the preview is at\n{wav}", parent=tab
            )

    tasks.submit(partial(ff.capture, cmd, timeout=120), on_done=done, name="AudioPreview")
