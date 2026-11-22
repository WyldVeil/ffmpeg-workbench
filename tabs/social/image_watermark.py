"""
tabs/social/image_watermark.py  -  Image Watermark

Put a logo (PNG with transparency, JPG, SVG-rendered PNG, or an animated
GIF/WebP) on a video: any corner or a custom spot, sized as a percentage of
the video's width, with opacity and an optional time range with fades.
Animated logos keep looping for the whole video.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.ops.image_watermark import POSITIONS, Logo, logo_graph
from core.scaling import px
from tabs import common
from tabs.base_tab import UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, SaveRow, here_button, scroll_body


class ImageWatermarkTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self, t("tab.image_watermark"), "Add a logo or badge on top of a video.", icon="🖼"
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.logo_row = FileRow(
            body, "Logo image:", kind="image", on_change=lambda _p: self._changed()
        )
        self.logo_row.pack(fill="x", padx=px(20), pady=px(2))
        self.compare = CompareView(body, 400, 225, after_label="With logo")
        self.compare.pack(pady=px(4))

        opt = tk.LabelFrame(body, text="  Placement  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.place_var = tk.StringVar(value=POSITIONS["br"][0])
        pb = ttk.Combobox(
            opt,
            textvariable=self.place_var,
            values=[v[0] for v in POSITIONS.values()],
            state="readonly",
            width=20,
        )
        pb.grid(row=0, column=0, sticky="w")
        pb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.cx_var, self.cy_var = tk.StringVar(value="50"), tk.StringVar(value="50")
        r = tk.Frame(opt)
        r.grid(row=0, column=1, sticky="w", padx=px(10))
        for label, var in (("X %", self.cx_var), ("Y %", self.cy_var)):
            tk.Label(r, text=label).pack(side="left", padx=px(4, 2))
            tk.Entry(r, textvariable=var, width=5).pack(side="left")
            var.trace_add("write", lambda *_a: self._changed())
        self.size_var = tk.DoubleVar(value=15)
        tk.Scale(
            opt,
            variable=self.size_var,
            from_=2,
            to=80,
            orient="horizontal",
            length=px(180),
            label="Width (% of video)",
            command=lambda _v: self._changed(),
        ).grid(row=1, column=0, sticky="w")
        self.opacity_var = tk.DoubleVar(value=0.85)
        tk.Scale(
            opt,
            variable=self.opacity_var,
            from_=0.05,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=px(160),
            label="Opacity",
            command=lambda _v: self._changed(),
        ).grid(row=1, column=1, sticky="w")
        self.margin_var = tk.DoubleVar(value=3)
        tk.Scale(
            opt,
            variable=self.margin_var,
            from_=0,
            to=15,
            resolution=0.5,
            orient="horizontal",
            length=px(140),
            label="Margin (%)",
            command=lambda _v: self._changed(),
        ).grid(row=1, column=2, sticky="w")
        tr = tk.Frame(opt)
        tr.grid(row=2, column=0, columnspan=3, sticky="w", pady=px(4))
        self.window_var = tk.BooleanVar(value=False)
        tk.Checkbutton(tr, text="Only from", variable=self.window_var, command=self._changed).pack(
            side="left"
        )
        self.from_var, self.to_var = tk.StringVar(value="0"), tk.StringVar(value="10")
        tk.Entry(tr, textvariable=self.from_var, width=9).pack(side="left", padx=px(2))
        here_button(
            tr,
            self.from_var,
            lambda: self.compare.position,
            self._window_on,
            "From the frame shown (i)",
        ).pack(side="left")
        tk.Label(tr, text="to").pack(side="left")
        tk.Entry(tr, textvariable=self.to_var, width=9).pack(side="left", padx=px(2))
        here_button(
            tr,
            self.to_var,
            lambda: self.compare.position,
            self._window_on,
            "To the frame shown (o)",
        ).pack(side="left")
        tk.Label(tr, text="  Fade (s):").pack(side="left")
        self.fade_var = tk.StringVar(value="0.5")
        tk.Entry(tr, textvariable=self.fade_var, width=5).pack(side="left")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, "logo"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="🖼  ADD LOGO",
            font=(UI_FONT, 12, "bold"),
            bg="#E91E63",
            fg="white",
            width=22,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def graph(self, info, logo_input="[1:v]"):
        w, h = common.even_size(info)
        place = next((k for k, v in POSITIONS.items() if v[0] == self.place_var.get()), "br")
        window = None
        if self.window_var.get():
            a = ff.parse_time(self.from_var.get() or "0")
            b = ff.parse_time(self.to_var.get())
            if b <= a:
                raise ValueError("'to' must be after 'from'.")
            window = (a, b)
        spec = Logo(
            pct=float(self.size_var.get()),
            opacity=float(self.opacity_var.get()),
            place=place,
            margin_pct=float(self.margin_var.get()),
            custom=(
                common.number(self.cx_var.get() or "50", "X %", 0, 100),
                common.number(self.cy_var.get() or "50", "Y %", 0, 100),
            ),
            window=window,
            fade=common.number(self.fade_var.get() or "0", "Fade", 0, 10),
        )
        return logo_graph(w, h, spec, logo_input)

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "logo"))

    def _window_on(self):
        self.window_var.set(True)
        self._changed()

    # i / o: the logo shows from / to the frame shown
    def mark_in(self):
        self.from_var.set(ff.format_time(self.compare.position))
        self._window_on()

    def mark_out(self):
        self.to_var.set(ff.format_time(self.compare.position))
        self._window_on()

    def _changed(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._refresh)

    def _refresh(self):
        self._job = None
        logo = self.logo_row.path
        if self._info is None or not logo:
            return
        try:
            g = self.graph(self._info, logo_input=fl.preview_movie(logo))
        except ValueError:
            return
        self.compare.set_filter(g.replace("[0:v]", "[in]").replace("[v]", ""), keep_time=True)

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        logo = self.logo_row.path
        linfo = common.check_source(self, logo, what="Logo image")
        if linfo is None:
            return
        try:
            graph = self.graph(info)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "logo", inputs=[logo])
        if not out:
            return
        animated = (
            logo.lower().endswith((".gif", ".webp", ".apng"))
            and (linfo.require_video().nb_frames or 0) != 1
        )
        if animated:
            logo_in = (
                ["-ignore_loop", "0", "-i", logo]
                if logo.lower().endswith(".gif")
                else ["-stream_loop", "-1", "-i", logo]
            )
        else:
            logo_in = ["-loop", "1", "-i", logo]
        common.render(
            self,
            common.RenderJob(
                src=src,
                info=info,
                out=out,
                graph=graph,
                extra_inputs=logo_in,
                name="Image Watermark",
                inputs=[logo],
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
