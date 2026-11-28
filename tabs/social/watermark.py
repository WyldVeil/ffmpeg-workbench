"""
tabs/social/watermark.py  -  Watermarker (text)

Burn a text watermark: any corner, centre or a custom spot, rotated if you
like, or moving - bouncing around like the DVD logo, or hopping to a new
place every few seconds.  Show it all the time or only at chosen times,
with soft fades.  Rendered by libass (core/ass.py), so any text works and
sizes scale with the video.
"""

from __future__ import annotations

import os
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

from core import ass
from core import ffmpeg as ff
from core.i18n import Labels, t, t_or
from core.ops.shared import RenderJob
from core.ops.text_watermark import PLACES, TextMark, build_script, parse_ranges
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit.tktyped import on_write
from tabs.kit.widgets import ColourButton
from tabs.widgets import CompareView, FileRow, output_section, scroll_body

#: where the text can go, shown in the current language
PLACE_LABELS = Labels("watermark.places", PLACES)


class TextBurnerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._job: str | None = None
        self._pdir: str | None = None
        self._pn = 0
        self._build_ui()

    def _build_ui(self) -> None:
        body = self._build_header()
        self._build_position_motion(body)
        self._build_render(body)

    def _build_header(self) -> tk.Frame:
        self.make_header(
            self,
            t("tab.watermarker"),
            t("watermark.burn_a_text_watermark_fixed_rotated_bouncing"),
            icon="©",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.compare = CompareView(body, 400, 225, after_label="With watermark")
        self.compare.pack(pady=px(4))

        ty = tk.LabelFrame(body, text=t("watermark.text"), padx=px(12), pady=px(6))
        ty.pack(fill="x", padx=px(20), pady=px(4))
        self.text_var = tk.StringVar(value=t_or("watermark.sample.my_channel", "© My Channel"))
        tk.Entry(ty, textvariable=self.text_var, width=40).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        self.font_var = tk.StringVar(value="Montserrat")
        ttk.Combobox(
            ty, textvariable=self.font_var, values=ass.families(), state="readonly", width=16
        ).grid(row=1, column=0, sticky="w", pady=px(4))
        self.size_var = tk.IntVar(value=48)
        tk.Scale(
            ty,
            variable=self.size_var,
            from_=12,
            to=200,
            orient="horizontal",
            length=px(160),
            label=t("watermark.size"),
            command=lambda _v: self._changed(),
        ).grid(row=1, column=1, sticky="w")
        self.opacity_var = tk.DoubleVar(value=0.8)
        tk.Scale(
            ty,
            variable=self.opacity_var,
            from_=0.1,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=px(140),
            label=t("pip.opacity_section"),
            command=lambda _v: self._changed(),
        ).grid(row=1, column=2, sticky="w")
        flags = tk.Frame(ty)
        flags.grid(row=2, column=0, columnspan=3, sticky="w")
        self.bold_var, self.italic_var = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        self.shadow_var, self.outline_var = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        for text, var in (
            ("Bold", self.bold_var),
            ("Italic", self.italic_var),
            ("Shadow", self.shadow_var),
            ("Outline", self.outline_var),
        ):
            tk.Checkbutton(flags, text=text, variable=var, command=self._changed).pack(side="left")
        self.col_btn = ColourButton(
            flags, "#FFFFFF", text=t("shortifier.colour"), on_change=lambda _c: self._changed()
        )
        return body

    def _build_position_motion(self, body: tk.Frame) -> None:
        self.col_btn.pack(side="left", padx=px(8))

        pos = tk.LabelFrame(body, text=t("watermark.position_motion"), padx=px(12), pady=px(6))
        pos.pack(fill="x", padx=px(20), pady=px(4))
        self.place_var = tk.StringVar(value=PLACE_LABELS["br"])
        pb = ttk.Combobox(
            pos,
            textvariable=self.place_var,
            values=list(PLACE_LABELS.values()),
            state="readonly",
            width=24,
        )
        pb.grid(row=0, column=0, sticky="w")
        pb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.angle_var = tk.StringVar(value="0")
        self.cx_var, self.cy_var = tk.StringVar(value="50"), tk.StringVar(value="50")
        self.every_var, self.rand_var = tk.StringVar(value="3"), tk.BooleanVar(value=False)
        row = tk.Frame(pos)
        row.grid(row=1, column=0, sticky="w", pady=px(4))
        for label, var, w in (
            ("Angle°", self.angle_var, 5),
            ("X %", self.cx_var, 5),
            ("Y %", self.cy_var, 5),
            ("Jump every (s)", self.every_var, 5),
        ):
            tk.Label(row, text=label).pack(side="left", padx=px(6, 2))
            tk.Entry(row, textvariable=var, width=w).pack(side="left")
            on_write(var, lambda: self._changed())
        tk.Checkbutton(
            row, text=t("watermark.random_1_2_that"), variable=self.rand_var, command=self._changed
        ).pack(side="left", padx=px(6))

        tm = tk.LabelFrame(body, text=t("watermark.when"), padx=px(12), pady=px(6))
        tm.pack(fill="x", padx=px(20), pady=px(4))
        self.always_var = tk.BooleanVar(value=True)
        tk.Radiobutton(
            tm,
            text=t("watermark.the_whole_video"),
            value=True,
            variable=self.always_var,
            command=self._changed,
        ).grid(row=0, column=0, sticky="w")
        tk.Radiobutton(
            tm,
            text=t("watermark.only_at"),
            value=False,
            variable=self.always_var,
            command=self._changed,
        ).grid(row=1, column=0, sticky="w")
        self.ranges_var = tk.StringVar(value="0-5, 20-25")
        tk.Entry(tm, textvariable=self.ranges_var, width=30).grid(row=1, column=1, sticky="w")
        tk.Label(tm, text="e.g. 0-5, 1:20-1:30", fg=CLR["fgdim"], font=(UI_FONT, 8)).grid(
            row=1, column=2
        )
        self.fade_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            tm, text=t("watermark.fade_in_and_out"), variable=self.fade_var, command=self._changed
        ).grid(row=2, column=0, sticky="w")
        for var in (self.text_var, self.font_var, self.ranges_var):
            on_write(var, lambda: self._changed())

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.src_row.path, "watermarked"),
        )

    def _build_render(self, body: tk.Frame) -> None:
        self.btn_render = self.make_render_btn(
            body, t("watermark.burn_watermark"), self._render, width=24
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def script(self, w: int, h: int, duration: float) -> ass.Script:
        place = PLACE_LABELS.code_of(self.place_var.get(), "br")
        every = common.number(self.every_var.get() or "3", t("watermark.jump_every_2"), 0.2, 3600)
        mark = TextMark(
            text=self.text_var.get(),
            place=place,
            font=self.font_var.get(),
            size=int(self.size_var.get()),
            colour=self.col_btn.colour,
            opacity=float(self.opacity_var.get()),
            bold=self.bold_var.get(),
            italic=self.italic_var.get(),
            shadow=self.shadow_var.get(),
            outline=self.outline_var.get(),
            angle=common.number(self.angle_var.get() or "0", t("watermark.angle"), -360, 360),
            custom=(
                common.number(self.cx_var.get() or "50", "X %", 0, 100),
                common.number(self.cy_var.get() or "50", "Y %", 0, 100),
            ),
            every=every,
            random_every=(every, every * 2) if self.rand_var.get() else None,
            ranges=None if self.always_var.get() else parse_ranges(self.ranges_var.get()),
            fade=self.fade_var.get(),
        )
        return build_script(mark, w, h, duration)

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "watermarked"))

    def _changed(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._refresh)

    def _refresh(self) -> None:
        self._job = None
        info = self._info
        if info is None or not info.has_video or not self.text_var.get().strip():
            return
        try:
            w, h = common.even_size(info)
            sc = self.script(w, h, info.duration)
        except ValueError:
            return
        if self._pdir is None:
            from core.paths import temp_dir

            self._pdir = temp_dir("wm_preview_")
        self._pn += 1
        path = sc.write(os.path.join(self._pdir, f"p{self._pn}.ass"))
        self.compare.set_filter(ass.subtitles_filter(path), keep_time=True)

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        if not self.text_var.get().strip():
            messagebox.showwarning(
                t("common.warning"), t("watermark.type_the_watermark_text"), parent=self
            )
            return
        w, h = common.even_size(info)
        try:
            sc = self.script(w, h, info.duration)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "watermarked")
        if not out:
            return
        from core.paths import temp_dir

        work = temp_dir("wm_")
        path = sc.write(os.path.join(work, "watermark.ass"))

        def done(rc: int) -> None:
            shutil.rmtree(work, ignore_errors=True)
            self.show_result(rc, out)

        common.render(
            self,
            RenderJob(
                src=src, info=info, out=out, chain=ass.subtitles_filter(path), name="Watermarker"
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            on_done=done,
        )

    def load_file(self, path: str | None = None) -> bool:
        if path:
            self.src_var.set(path)
            return True
        return False

    def on_app_closing(self) -> None:
        if self._pdir:
            shutil.rmtree(self._pdir, ignore_errors=True)
            self._pdir = None  # a new one is made if the page is used again
