"""
tabs/transcoder/downsizer.py  -  Universal Downsizer

Make any video fit a file-size limit (Discord, email, upload caps...) at the
best quality that fits.

* The bitrate budget comes from the target size and the length (with a
  margin for the container); audio gets a share that suits the budget.
* Resolution and frame rate are lowered only as far as needed - judged by
  bits per pixel for the chosen codec - and only when allowed.
* "Best quality" encodes two-pass on the CPU (accurate size); "Fast" uses the
  GPU encoder when there is one.  If the file still comes out over the
  limit it is re-encoded at a lower bitrate automatically, so the result is
  never over the limit.
* HDR sources are tone-mapped to SDR for H.264 (and kept HDR for HEVC/AV1).
* Optional start/end trim for clips that are too long to fit nicely.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from core import ffmpeg as ff
from core.i18n import t
from core.ops.downsizer import Goal, Plan, encode_to_size, plan
from core.ops.shared import Steps
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, BaseTab
from tabs.kit.time_fields import RangePicker
from tabs.kit.tktyped import on_write
from tabs.widgets import FileRow, save_section, scroll_body

if TYPE_CHECKING:
    from core.queue_manager import TaskContext

TARGETS = [
    ("8 MB", 8),
    ("10 MB - Discord", 10),
    ("18 MB - safe for email", 18),
    ("25 MB", 25),
    ("50 MB - Discord Nitro Basic", 50),
    ("100 MB", 100),
    ("500 MB - Discord Nitro", 500),
    ("Custom size", 0),
]
CODECS = {
    "h264": "H.264 - plays everywhere (recommended)",
    "hevc": "HEVC (H.265) - smaller, newer devices",
    "av1": "AV1 - smallest, newest devices",
}
AUDIO_MODES = {
    "auto": "Re-encode to fit (AAC)",
    "copy": "Keep the original audio",
    "none": "Remove the audio",
}


class UniversalDownsizerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._info: ff.MediaInfo | None = None
        self._job: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        body, cb, opt, tb = self._build_header()
        self._build_output(body, cb, opt, tb)
        self._changed()

    def _build_header(self) -> tuple[tk.Frame, ttk.Combobox, tk.LabelFrame, ttk.Combobox]:
        self.make_header(
            self,
            t("tab.universal_downsizer"),
            "Shrink a video to fit a size limit, at the best quality that fits.",
            icon="🗜",
        )
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self._src_var = self.src_row.var
        self.info_lbl = tk.Label(body, text="", fg=CLR["fgdim"], anchor="w")
        self.info_lbl.pack(fill="x", padx=px(20))

        opt = tk.LabelFrame(body, text="  Target  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(6))

        self.target_var = tk.StringVar(value=TARGETS[1][0])
        tb = ttk.Combobox(
            opt,
            textvariable=self.target_var,
            values=[x[0] for x in TARGETS],
            state="readonly",
            width=28,
        )
        self._target_mb = tk.StringVar(value="10")
        self.custom_entry = tk.Entry(opt, textvariable=self._target_mb, width=7)
        self.form_row(
            opt, 0, "Size limit:", tb, self.custom_entry, tk.Label(opt, text="MB"), label_width=14
        )
        self.codec_var = tk.StringVar(value=CODECS["h264"])
        cb = ttk.Combobox(
            opt,
            textvariable=self.codec_var,
            values=list(CODECS.values()),
            state="readonly",
            width=40,
        )
        self.form_row(opt, 1, "Codec:", cb, label_width=14)
        self.speed_var = tk.StringVar(value="quality")
        sp = tk.Frame(opt)
        tk.Radiobutton(
            sp,
            text="Best quality (two-pass, CPU)",
            variable=self.speed_var,
            value="quality",
            command=self._changed,
        ).pack(side="left")
        tk.Radiobutton(
            sp,
            text="Fast (GPU when available)",
            variable=self.speed_var,
            value="fast",
            command=self._changed,
        ).pack(side="left", padx=px(10))
        self.form_row(opt, 2, "Encoding:", sp, label_width=14)
        self.audio_var = tk.StringVar(value=AUDIO_MODES["auto"])
        return body, cb, opt, tb

    def _build_output(
        self, body: tk.Frame, cb: ttk.Combobox, opt: tk.LabelFrame, tb: ttk.Combobox
    ) -> None:
        ab = ttk.Combobox(
            opt,
            textvariable=self.audio_var,
            values=list(AUDIO_MODES.values()),
            state="readonly",
            width=28,
        )
        self.form_row(opt, 3, "Audio:", ab, label_width=14)
        self._allow_res = tk.BooleanVar(value=True)
        self._allow_fps = tk.BooleanVar(value=True)
        perm = tk.Frame(opt)
        tk.Checkbutton(
            perm, text="May lower the resolution", variable=self._allow_res, command=self._changed
        ).pack(side="left")
        tk.Checkbutton(
            perm,
            text="May lower the frame rate (above 30 fps only)",
            variable=self._allow_fps,
            command=self._changed,
        ).pack(side="left", padx=px(10))
        self.form_row(opt, 4, "Allowed:", perm, label_width=14)
        self.trim = RangePicker(
            opt,
            start="",
            end_hint="(optional - leave empty for the whole video)",
            source=lambda: self.src_row.path,
        )
        self.form_row(opt, 5, "Trim:", self.trim, label_width=14)
        for w in (tb, cb, ab):
            w.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        for v in (self._target_mb, self.trim.start.var, self.trim.end.var):
            on_write(v, lambda: self._changed())
        self.plan_lbl = tk.Label(
            opt, text="", fg=CLR["accent"], justify="left", anchor="w", wraplength=px(640)
        )
        self.plan_lbl.grid(row=6, column=0, columnspan=6, sticky="w", pady=px(6, 0))

        self.out_row = save_section(
            body,
            t("common.output_file"),
            ext=".mp4",
            suggest=lambda: self.suggest_output(self.src_row.path, "small", ".mp4"),
        )
        self._out_var = self.out_row.var
        self.btn_render = self.make_render_btn(
            body, t("downsizer.render_button"), self._start_render, width=30
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=7, padx=0, pady=0)

    # ── State ─────────────────────────────────────────────────────────
    def _key(self, table: dict[str, str], var: tk.StringVar, default: str) -> str:
        return next((k for k, v in table.items() if v == var.get()), default)

    def _target_bytes(self) -> int:
        mb = dict(TARGETS).get(self.target_var.get(), 0)
        if not mb:
            mb = common.number(self._target_mb.get(), "Size limit", 0.1, 100000)
        return int(mb * 1_000_000)  # decimal MB: also fits limits counted in MiB

    def _trim(self, info: ff.MediaInfo) -> tuple[float, float]:
        a, end = self.trim.span(info.duration)
        b = info.duration if end is None else end
        if b - a < 0.5:
            raise ValueError("The trimmed part must be at least half a second long.")
        return a, b

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        info = self._info
        if info is None or not info.has_video:
            self.info_lbl.config(text="")
        else:
            v = info.require_video()
            size_mb = os.path.getsize(path) / 1e6 if os.path.isfile(path) else 0
            hdr = "  HDR" if v.is_hdr else ""
            self.info_lbl.config(
                text=f"{ff.format_time(info.duration)}  ·  {v.width}×{v.height}  ·  "
                f"{v.fps:.3g} fps  ·  {v.codec}{hdr}  ·  {size_mb:.1f} MB now"
            )
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "small", ".mp4"))
        self._changed()

    def _plan(self) -> tuple[Plan, tuple[float, float]]:
        info = self._info
        if info is None or not info.has_video:
            raise ValueError("Choose a video first.")
        a, b = self._trim(info)
        codec = self._key(CODECS, self.codec_var, "h264")
        audio_mode = self._key(AUDIO_MODES, self.audio_var, "auto")
        src_kbps = int((info.require_audio().bit_rate or 0) / 1000) if info.has_audio else 0
        goal = Goal(
            self._target_bytes(),
            codec=codec,
            audio_mode=audio_mode,
            allow_res=self._allow_res.get(),
            allow_fps=self._allow_fps.get(),
        )
        p = plan(
            goal,
            b - a,
            common.even_size(info),
            info.require_video().fps,
            has_audio=info.has_audio,
            audio_src_kbps=src_kbps,
        )
        return p, (a, b)

    def _changed(self) -> None:
        self.custom_entry.config(
            state="normal" if dict(TARGETS).get(self.target_var.get(), 0) == 0 else "disabled"
        )
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(150, self._show_plan)

    def _show_plan(self) -> None:
        self._job = None
        if self._info is None:
            self.plan_lbl.config(text="")
            return
        try:
            p, (a, b) = self._plan()
        except ValueError as exc:
            self.plan_lbl.config(text=f"⚠  {exc}", fg=CLR["orange"])
            return
        cur = os.path.getsize(self.src_row.path) if os.path.isfile(self.src_row.path) else 0
        if cur and cur <= self._target_bytes() and (a, b) == (0.0, self._info.duration):
            msg = "The file is already under this size - it will still be re-encoded."
        else:
            audio = (
                f" + audio {p.audio_kbps} kb/s{' mono' if p.mono else ''}" if p.audio_kbps else ""
            )
            msg = (
                f"Plan: {p.width}×{p.height} at {p.fps:.3g} fps, video {p.video_kbps:,} kb/s{audio}"
                + ("\n" + "\n".join(p.notes) if p.notes else "")
            )
        self.plan_lbl.config(text=msg, fg=CLR["accent"])

    # ── Render ────────────────────────────────────────────────────────
    def _start_render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        self._info = info
        try:
            p, (start, end) = self._plan()
            target = self._target_bytes()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = self.out_row.path or self.suggest_output(src, "small", ".mp4")
        if not out.lower().endswith((".mp4", ".mkv", ".mov")):
            out = os.path.splitext(out)[0] + ".mp4"
        out = self.confirm_output(out, [src], default_ext=".mp4")
        if not out:
            return
        self.out_row.set(out)
        fast = self.speed_var.get() == "fast"
        self.log(
            self.console,
            f"Target {target / 1e6:g} MB · {p.width}×{p.height} @ {p.fps:.3g} fps · "
            f"video {p.video_kbps} kb/s · audio {p.audio_kbps} kb/s",
        )
        for note in p.notes:
            self.log(self.console, "  " + note)
        length = end - start

        def work(_p: object, _c: object, ctx: TaskContext) -> int:
            def runner(cmd: list[str], duration: float, lo: float, hi: float) -> None:
                ctx.span(lo, hi).run(cmd, duration=duration)

            steps = Steps(runner, ctx.log, ctx.tempdir())
            return encode_to_size(
                src, ctx.output(), info, p, steps, fast=fast, start=start, end=end
            )

        common.queue_job(
            self,
            common.Job(name="Downsizer", out=out, work=work, duration=length, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
