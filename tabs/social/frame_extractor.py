"""
tabs/social/frame_extractor.py  -  Frame Extractor

Pull stills out of a video - scrub the preview to find the frame you want.

  1. This frame   - the frame under the preview slider (or a typed time)
  2. Every N sec  - frames at a regular interval across a range
  3. Scene starts - the first frame of every scene (minimum scene length
                    respected, first scene included)
  4. Contact sheet - a grid of evenly spaced frames, each labelled with its
                    own timestamp, with a header line

Formats: PNG, JPEG and WebP (quality 1-100 means what it says), TIFF.
Image sets go to a fresh folder per run, so earlier exports are never mixed
with or overwritten by new ones.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import contact_sheet as cs
from core.ops import frame_extractor as fe
from core.ops.frame_extractor import quality_args
from core.outputs import fresh_folder
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab, add_tooltip
from tabs.kit.time_fields import MomentPicker, RangePicker
from tabs.widgets import FileRow, FolderRow, FramePreview, scroll_body

FORMATS = {"png": "PNG (lossless)", "jpg": "JPEG", "webp": "WebP", "tiff": "TIFF (print)"}
MODES = {
    "single": "This frame",
    "interval": "Every N seconds",
    "scenes": "Scene starts",
    "sheet": "Contact sheet",
}


class FrameExtractorTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        body, opts, p = self._build_header()
        self._build_every(opts, p)
        self._build_dir(body, opts)

    def _build_header(self) -> tuple[tk.Frame, tk.LabelFrame, tk.Frame]:
        self.make_header(
            self,
            t("tab.frame_extractor"),
            t("frame_extractor.subtitle")
            if t("frame_extractor.subtitle") != "frame_extractor.subtitle"
            else "Thumbnails, storyboards and contact sheets.",
            icon="🖼",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 480, 270)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0.0)
        bar = tk.Frame(pv)
        bar.pack(fill="x")
        self.slider = ttk.Scale(
            bar,
            from_=0,
            to=1,
            variable=self.pos_var,
            length=px(400),
            command=lambda _v: self._moved(),
        )
        self.slider.pack(side="left", fill="x", expand=True)
        framestep.slider(self.slider, self._fps)
        for label, frames, seconds, tip in (
            ("◀◀", 0, -1, "One second back (Shift+,)"),
            ("◀", -1, 0, "One frame back (,)"),
            ("▶", 1, 0, "One frame forward (.)"),
            ("▶▶", 0, 1, "One second forward (Shift+.)"),
        ):
            b = tk.Button(
                bar,
                text=label,
                relief="flat",
                width=3,
                command=lambda f=frames, s=seconds: framestep.step(self.slider, f, s),
            )
            b.pack(side="left")
            add_tooltip(b, tip)

        opts = tk.LabelFrame(body, text="  Extract  ", padx=px(12), pady=px(8))
        opts.pack(fill="x", padx=px(20), pady=px(6))
        self.mode_var = tk.StringVar(value="single")
        mrow = tk.Frame(opts)
        mrow.pack(fill="x")
        for key, label in MODES.items():
            tk.Radiobutton(
                mrow, text=label, value=key, variable=self.mode_var, command=self._mode_changed
            ).pack(side="left", padx=px(6))

        self.panels: dict[str, tk.Frame] = {}
        p = self.panels["single"] = tk.Frame(opts)
        tk.Label(p, text="Time:").pack(side="left")
        self.moment = MomentPicker(p, value="0", width=14, on_change=self._time_typed)
        return body, opts, p

    def _build_every(self, opts: tk.LabelFrame, p: tk.Frame) -> None:
        self.moment.pack(side="left", padx=px(6))
        tk.Label(
            p, text="(follows the slider; type m:ss.ms to jump)", fg=CLR["fgdim"], font=(UI_FONT, 8)
        ).pack(side="left")

        p = self.panels["interval"] = tk.Frame(opts)
        self.every_var = tk.StringVar(value="1")
        tk.Label(p, text="Every (s):").pack(side="left", padx=px(8, 2))
        tk.Entry(p, textvariable=self.every_var, width=6).pack(side="left")
        tk.Label(p, text="From:").pack(side="left", padx=px(8, 2))
        self.part = RangePicker(p, end_hint="(empty = end)", here=lambda: float(self.pos_var.get()))
        self.part.pack(side="left")

        p = self.panels["scenes"] = tk.Frame(opts)
        tk.Label(p, text="Sensitivity:").pack(side="left")
        self.scene_var = tk.DoubleVar(value=0.3)
        tk.Scale(
            p,
            variable=self.scene_var,
            from_=0.05,
            to=0.8,
            resolution=0.05,
            orient="horizontal",
            length=px(180),
        ).pack(side="left", padx=px(6))
        tk.Label(p, text="(lower = more scenes)   Min. scene (s):").pack(side="left")
        self.min_scene_var = tk.StringVar(value="2")
        tk.Entry(p, textvariable=self.min_scene_var, width=5).pack(side="left", padx=px(4))

        p = self.panels["sheet"] = tk.Frame(opts)
        self.cols_var, self.rows_var, self.tile_w_var = (
            tk.StringVar(value="5"),
            tk.StringVar(value="4"),
            tk.StringVar(value="320"),
        )
        for label, var in (
            ("Columns:", self.cols_var),
            ("Rows:", self.rows_var),
            ("Thumb width:", self.tile_w_var),
        ):
            tk.Label(p, text=label).pack(side="left", padx=px(8, 2))
            tk.Entry(p, textvariable=var, width=5).pack(side="left")
        self.stamp_var = tk.BooleanVar(value=True)
        tk.Checkbutton(p, text="Timestamps", variable=self.stamp_var).pack(side="left", padx=px(8))

        frow = tk.Frame(opts)
        frow.pack(fill="x", pady=px(8, 0))
        tk.Label(frow, text="Format:").pack(side="left")
        self.fmt_var = tk.StringVar(value=FORMATS["png"])
        ttk.Combobox(
            frow,
            textvariable=self.fmt_var,
            values=list(FORMATS.values()),
            state="readonly",
            width=16,
        ).pack(side="left", padx=px(6))
        tk.Label(frow, text="Quality (1-100):").pack(side="left", padx=px(10, 2))
        self.quality_var = tk.IntVar(value=92)
        tk.Scale(
            frow, variable=self.quality_var, from_=1, to=100, orient="horizontal", length=px(160)
        ).pack(side="left")

    def _build_dir(self, body: tk.Frame, opts: tk.LabelFrame) -> None:
        self.dir_var = tk.StringVar()
        FolderRow(opts, "Save to folder:", self.dir_var, width=50, label_width=14).pack(
            fill="x", pady=px(6, 0)
        )

        self.btn_render = self.make_render_btn(
            body, t("frame_extractor.extract_button"), self._render, width=24
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)
        self._mode_changed()

    # ── interaction ──────────────────────────────────────────────────────────

    def _mode_changed(self) -> None:
        for p in self.panels.values():
            p.pack_forget()
        self.panels[self.mode_var.get()].pack(fill="x", pady=px(6, 0))

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.pos_var.set(min(dur * 0.1, 10.0))
        self.preview.load(path, dur, float(self.pos_var.get()))
        self._moved()
        if not self.dir_var.get().strip():
            from core.outputs import default_output_folder

            self.dir_var.set(default_output_folder(path))

    def _fps(self) -> float:
        return (self._info.require_video().fps if self._info and self._info.video else 0) or 30.0

    def _moved(self) -> None:
        pos = float(self.pos_var.get())
        self._syncing = True
        self.moment.set(pos)
        self._syncing = False
        self.preview.show(pos)

    def _time_typed(self) -> None:
        if getattr(self, "_syncing", False):
            return
        try:
            pos = self.moment.seconds()
        except ValueError:
            return
        if pos is None:
            return
        self.pos_var.set(pos)
        self.preview.show(pos)

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        self._info = info
        ext = next((k for k, v in FORMATS.items() if v == self.fmt_var.get()), "png")
        q = quality_args(ext, self.quality_var.get())
        folder = self.dir_var.get().strip() or os.path.dirname(src)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(t("common.error"), f"Cannot use {folder}:\n{exc}", parent=self)
            return
        base = os.path.splitext(os.path.basename(src))[0]
        mode = self.mode_var.get()
        try:
            if mode == "single":
                self._single(src, info, folder, base, ext, q)
            elif mode == "interval":
                self._interval(src, info, folder, base, ext, q)
            elif mode == "scenes":
                self._scenes(src, info, folder, base, ext, q)
            else:
                self._sheet(src, info, folder, base, ext, q)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)

    def _single(
        self, src: str, info: ff.MediaInfo, folder: str, base: str, ext: str, q: list[str]
    ) -> None:
        at = self.moment.seconds(0.0, "Time") or 0.0
        at = max(0.0, min(at, max(0.0, info.duration - 1.0 / self._fps())))
        stamp = ff.format_time(at).replace(":", "-")
        from core.outputs import unique_path

        out = unique_path(os.path.join(folder, f"{base}_{stamp}.{ext}"))
        cmd = fe.single_cmd(src, at, out, q)
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=0.1,
            output=out,
            inputs=[src],
            name="Frame Extractor",
        )

    def _interval(
        self, src: str, info: ff.MediaInfo, folder: str, base: str, ext: str, q: list[str]
    ) -> None:
        every = common.number(self.every_var.get(), "Every", 0.01, 3600)
        start, until = self.part.span(info.duration)
        end = info.duration if until is None else until
        out_dir = fresh_folder(folder, f"{base} frames")
        # image2 patterns treat every '%' as a format code - escape real ones
        pattern = os.path.join(out_dir, base).replace("%", "%%") + f"_%05d.{ext}"
        cmd = fe.interval_cmd(src, (start, end), every, self._fps(), pattern, q)
        self.log(self.console, f"One frame every {every:g}s → {out_dir}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out_dir),
            btn=self.btn_render,
            duration=end - start,
            output=out_dir,
            inputs=[src],
            name="Frame Extractor",
        )

    def _scenes(
        self, src: str, info: ff.MediaInfo, folder: str, base: str, ext: str, q: list[str]
    ) -> None:
        threshold = float(self.scene_var.get())
        min_len = common.number(self.min_scene_var.get() or "0", "Min. scene", 0, 3600)
        out_dir = fresh_folder(folder, f"{base} scenes")
        dur = info.duration
        job = fe.SceneFrames(
            src=src,
            threshold=threshold,
            min_len=min_len,
            fps=self._fps(),
            duration=dur,
            pattern=os.path.join(out_dir, base).replace("%", "%%") + f"_scene_%04d.{ext}",
            quality=tuple(q),
        )
        self.log(self.console, f"Finding scenes (sensitivity {threshold:.2f}) → {out_dir}")
        common.queue_job(
            self,
            common.Job(
                name="Scene frames", out=out_dir, work=job.run, duration=dur * 1.3, inputs=[src]
            ),
            btn=self.btn_render,
            console=self.console,
        )

    def _sheet(
        self, src: str, info: ff.MediaInfo, folder: str, base: str, ext: str, _q: list[str]
    ) -> None:
        """A contact sheet (the Contact Sheet tool's engine, its default look)."""
        from core.outputs import unique_path

        layout = cs.Layout(
            cols=int(common.number(self.cols_var.get(), "Columns", 1, 12)),
            rows=int(common.number(self.rows_var.get(), "Rows", 1, 20)),
            tile_width=int(common.number(self.tile_w_var.get(), "Thumb width", 64, 1920)),
        )
        corner = cs.Corner.BOTTOM_RIGHT if self.stamp_var.get() else cs.Corner.OFF
        out = unique_path(os.path.join(folder, f"{base}_contact_sheet.{ext}"))
        job = cs.SheetJob(
            info=info,
            layout=layout,
            labels=cs.Labels(timestamps=corner),
            picking=cs.Picking(),
            threshold=None,
            cuts=None,
            quality=int(self.quality_var.get()),
        )
        self.log(self.console, f"Contact sheet {layout.cols}×{layout.rows} → {out}")
        common.queue_job(
            self,
            common.Job(name="Contact sheet", out=out, work=job.run, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
