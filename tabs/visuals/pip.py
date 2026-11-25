"""
tabs/visuals/pip.py  -  Picture-in-Picture

Put one video inside another: size as a share of the background's width
(the inset keeps its own shape), any corner or a custom spot, border,
rounded corners, opacity, and a time window - the inset starts playing when
it appears, can fade or slide in and out, and the background's length and
sound are kept (or the inset's sound, or both mixed).
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.ops import pip as pip_ops
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import BaseTab
from tabs.kit.time_fields import RangePicker
from tabs.kit.widgets import ColourButton
from tabs.widgets import CompareView, FileRow, output_section, scroll_body

POSITIONS = {
    "tl": "Top left",
    "tc": "Top centre",
    "tr": "Top right",
    "ml": "Middle left",
    "c": "Centre",
    "mr": "Middle right",
    "bl": "Bottom left",
    "bc": "Bottom centre",
    "br": "Bottom right",
    "custom": "Custom (%)",
}
ANIMS = {
    "none": "Just appear",
    "fade": "Fade in and out",
    "left": "Slide in from the left",
    "right": "Slide in from the right",
    "top": "Slide in from the top",
    "bottom": "Slide in from the bottom",
}
AUDIO = {"bg": "Background's sound", "fg": "Inset's sound", "mix": "Both mixed", "none": "No sound"}


class PIPTab(BaseTab):
    # i / o: the inset shows from / until the frame shown
    def mark_in(self) -> None:
        self.window.start.use_here()

    def mark_out(self) -> None:
        self.window.end.use_here()

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._bg = self._fg = None
        self._job: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        body, ch, opt, pb, pr = self._build_header()
        self._build_output(body, ch, opt, pb, pr)
        self._build_console(body)

    def _build_header(
        self,
    ) -> tuple[tk.Frame, Callable[..., None], tk.LabelFrame, ttk.Combobox, tk.Frame]:
        self.make_header(
            self, t("tab.picture_in_picture"), t("pip.put_one_video_inside_another"), icon="🖼"
        )
        body = scroll_body(self)
        self.bg_row = FileRow(
            body,
            t("pip.background_video"),
            self.src_var,
            on_change=self._bg_changed,
            label_width=18,
        )
        self.bg_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.src_row = self.bg_row
        self.fg_row = FileRow(
            body, t("pip.inset_video"), on_change=self._fg_changed, label_width=18
        )
        self.fg_row.pack(fill="x", padx=px(20), pady=px(2))
        self.compare = CompareView(body, 400, 225, after_label="Result")
        self.compare.pack(pady=px(4))
        opt = tk.LabelFrame(body, text=t("pip.inset"), padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))

        def ch(*_a: object) -> None:
            self._changed()

        self.size_var = tk.DoubleVar(value=30)
        tk.Scale(
            opt,
            variable=self.size_var,
            from_=5,
            to=90,
            orient="horizontal",
            length=px(180),
            label=t("pip.width_of_background"),
            command=ch,
        ).grid(row=0, column=0, sticky="w")
        self.margin_var = tk.DoubleVar(value=3)
        tk.Scale(
            opt,
            variable=self.margin_var,
            from_=0,
            to=15,
            resolution=0.5,
            orient="horizontal",
            length=px(140),
            label=t("image_watermark.margin"),
            command=ch,
        ).grid(row=0, column=1, sticky="w")
        self.opacity_var = tk.DoubleVar(value=1.0)
        tk.Scale(
            opt,
            variable=self.opacity_var,
            from_=0.1,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=px(140),
            label=t("pip.opacity_section"),
            command=ch,
        ).grid(row=0, column=2, sticky="w")
        pr = tk.Frame(opt)
        pr.grid(row=1, column=0, columnspan=3, sticky="w", pady=px(3))
        tk.Label(pr, text=t("side_by_side.label_position_label")).pack(side="left")
        self.place_var = tk.StringVar(value=POSITIONS["br"])
        pb = ttk.Combobox(
            pr,
            textvariable=self.place_var,
            values=list(POSITIONS.values()),
            state="readonly",
            width=16,
        )
        return body, ch, opt, pb, pr

    def _build_output(
        self,
        body: tk.Frame,
        ch: Callable[..., None],
        opt: tk.LabelFrame,
        pb: ttk.Combobox,
        pr: tk.Frame,
    ) -> None:
        pb.pack(side="left", padx=px(4))
        pb.bind("<<ComboboxSelected>>", ch)
        self.cx_var, self.cy_var = tk.StringVar(value="50"), tk.StringVar(value="50")
        for label, var in (("X %", self.cx_var), ("Y %", self.cy_var)):
            tk.Label(pr, text=label).pack(side="left", padx=px(6, 2))
            tk.Entry(pr, textvariable=var, width=5).pack(side="left")
            var.trace_add("write", ch)
        br = tk.Frame(opt)
        br.grid(row=2, column=0, columnspan=3, sticky="w", pady=px(3))
        tk.Label(br, text=t("pip.border_px")).pack(side="left")
        self.border_var = tk.StringVar(value="0")
        tk.Entry(br, textvariable=self.border_var, width=4).pack(side="left", padx=px(4))
        self.colour_btn = ColourButton(
            br, "#FFFFFF", text=t("shortifier.colour"), on_change=lambda _c: self._changed()
        )
        self.colour_btn.pack(side="left")
        tk.Label(br, text=t("pip.rounded_corners_px")).pack(side="left")
        self.radius_var = tk.StringVar(value="0")
        tk.Entry(br, textvariable=self.radius_var, width=4).pack(side="left", padx=px(4))
        for v in (self.border_var, self.radius_var):
            v.trace_add("write", ch)
        tr = tk.Frame(opt)
        tr.grid(row=3, column=0, columnspan=3, sticky="w", pady=px(3))
        tk.Label(tr, text=t("pip.show_from")).pack(side="left")
        self.window = RangePicker(
            tr,
            joiner="until",
            end_hint="(empty = until the inset ends)",
            here=lambda: self.compare.position,
            on_change=self._changed,
        )
        self.window.pack(side="left")
        ar = tk.Frame(opt)
        ar.grid(row=4, column=0, columnspan=3, sticky="w", pady=px(3))
        self.anim_var = tk.StringVar(value=ANIMS["none"])
        ttk.Combobox(
            ar, textvariable=self.anim_var, values=list(ANIMS.values()), state="readonly", width=22
        ).pack(side="left")
        tk.Label(ar, text="over").pack(side="left", padx=px(4))
        self.anim_dur_var = tk.StringVar(value="0.5")
        tk.Entry(ar, textvariable=self.anim_dur_var, width=5).pack(side="left")
        tk.Label(ar, text=t("pip.s_sound")).pack(side="left")
        self.audio_var = tk.StringVar(value=AUDIO["bg"])
        ttk.Combobox(
            ar, textvariable=self.audio_var, values=list(AUDIO.values()), state="readonly", width=20
        ).pack(side="left", padx=px(4))

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.bg_row.path, "pip"),
        )
        self.btn_render = self.make_render_btn(
            body, t("pip.make_picture_in_picture"), self._render, width=28
        )

    def _build_console(self, body: tk.Frame) -> None:
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    def _bg_changed(self, path: str) -> None:
        self._bg = ff.try_probe(path)
        self.compare.load(path)
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "pip"))
        self._changed()

    def _fg_changed(self, path: str) -> None:
        self._fg = ff.try_probe(path)
        self._changed()

    def spec(self) -> pip_ops.PipSpec:
        if self._bg is None or not self._bg.has_video or self._fg is None or not self._fg.has_video:
            raise ValueError("Choose a background video and an inset video.")
        start, end = self.window.span()
        if end is None and self._fg.duration:
            end = min(start + self._fg.duration, self._bg.duration or start + self._fg.duration)
        return pip_ops.PipSpec(
            bg_size=common.even_size(self._bg),
            fg_size=common.even_size(self._fg),
            pct=float(self.size_var.get()),
            place=next((k for k, v in POSITIONS.items() if v == self.place_var.get()), "br"),
            custom=(
                common.number(self.cx_var.get() or "50", "X %", 0, 100),
                common.number(self.cy_var.get() or "50", "Y %", 0, 100),
            ),
            margin_pct=float(self.margin_var.get()),
            border=int(
                common.number(self.border_var.get() or "0", t("contact_sheet.border"), 0, 100)
            ),
            border_colour=self.colour_btn.rgb,
            radius=int(common.number(self.radius_var.get() or "0", t("pip.corners"), 0, 500)),
            opacity=float(self.opacity_var.get()),
            start=start,
            end=end,
            anim=next((k for k, v in ANIMS.items() if v == self.anim_var.get()), "none"),
            anim_dur=common.number(
                self.anim_dur_var.get() or "0.5", t("pip.animation_length"), 0.05, 10
            ),
        )

    def _changed(self, *_a: object) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(300, self._refresh)

    def _refresh(self) -> None:
        self._job = None
        try:
            spec = self.spec()
        except ValueError:
            return
        fg = fl.preview_movie(self.fg_row.path)
        g = pip_ops.pip_graph(spec, "[in]", fg, "")
        self.compare.set_filter(g, keep_time=True)

    def _render(self) -> None:
        src, fg = self.bg_row.path, self.fg_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        fginfo = common.check_source(self, fg, what="Inset video")
        if fginfo is None:
            return
        self._bg, self._fg = info, fginfo
        try:
            spec = self.spec()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "pip", inputs=[fg])
        if not out:
            return
        mode = next((k for k, v in AUDIO.items() if v == self.audio_var.get()), "bg")
        dur = info.duration
        sound, audio = pip_ops.pip_audio(
            mode, spec.start, dur, fg_audio=fginfo.has_audio, bg_audio=info.has_audio
        )
        graph = pip_ops.pip_graph(spec) + sound
        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                graph=graph,
                extra_files=[fg],
                name="Picture-in-Picture",
                audio=audio,
                limit=dur,
                duration=dur,
                inputs=[fg],
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
