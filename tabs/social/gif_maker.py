"""
tabs/social/gif_maker.py  -  Pro GIF Maker

High-quality GIFs (or animated WebP) from any clip: a palette made for the
clip itself (palettegen/paletteuse in one pass), proper dithering choices,
frame rate, width, loop count and an optional size target - the GIF is
re-made smaller automatically until it fits.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops.gif_maker import Animation, make_animation
from core.ops.shared import Steps
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import FileRow, FramePreview, SaveRow, scroll_body

DITHERS = {
    "sierra2_4a": "Sierra (smooth, recommended)",
    "floyd_steinberg": "Floyd-Steinberg (detailed)",
    "bayer2": "Bayer pattern - fine",
    "bayer4": "Bayer pattern - coarse (smaller files)",
    "none": "None (posterised, smallest)",
}


class GIFMakerTab(BaseTab):
    # i / o (and the buttons): the GIF starts / ends at the frame shown
    def mark_in(self):
        self.start_var.set(ff.format_time(float(self.pos_var.get())))

    def mark_out(self):
        try:
            start = ff.parse_time(self.start_var.get() or "0")
        except ValueError:
            start = 0.0
        length = float(self.pos_var.get()) - start
        if length > 0:
            self.dur_var.set(f"{length:.3f}".rstrip("0").rstrip("."))

    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.pro_gif_maker"), t("gif_maker.subtitle"), icon="🎞")
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        pv = tk.Frame(body)
        pv.pack(pady=px(4))
        self.preview = FramePreview(pv, 400, 225)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0)
        bar = tk.Frame(pv)
        bar.pack(fill="x")
        self.slider = ttk.Scale(
            bar,
            from_=0,
            to=1,
            variable=self.pos_var,
            command=lambda _v: self.preview.show(float(self.pos_var.get())),
        )
        self.slider.pack(side="left", fill="x", expand=True)
        framestep.slider(self.slider, self.preview)  # , and . step a frame
        tk.Button(bar, text="Start here", relief="flat", command=self.mark_in).pack(side="left")
        tk.Button(bar, text="End here", relief="flat", command=self.mark_out).pack(side="left")

        opt = tk.LabelFrame(body, text="  GIF  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))

        def row(r, label, *widgets):
            tk.Label(opt, text=label, width=12, anchor="w").grid(
                row=r, column=0, sticky="w", pady=px(2)
            )
            for i, w in enumerate(widgets):
                w.grid(row=r, column=1 + i, sticky="w", padx=px(0, 6), pady=px(2))

        self.start_var, self.dur_var = tk.StringVar(value="0"), tk.StringVar(value="4")
        row(
            0,
            "Start:",
            tk.Entry(opt, textvariable=self.start_var, width=11),
            tk.Label(opt, text="Length (s):"),
            tk.Entry(opt, textvariable=self.dur_var, width=6),
        )
        self.fps_var, self.width_var = tk.StringVar(value="15"), tk.StringVar(value="480")
        row(
            1,
            "Frame rate:",
            ttk.Combobox(
                opt,
                textvariable=self.fps_var,
                values=["8", "10", "12", "15", "20", "24", "30"],
                width=5,
            ),
            tk.Label(opt, text="Width (px):"),
            ttk.Combobox(
                opt,
                textvariable=self.width_var,
                values=["240", "320", "400", "480", "640", "800"],
                width=6,
            ),
        )
        self.colours_var = tk.StringVar(value="256")
        self.dither_var = tk.StringVar(value=DITHERS["sierra2_4a"])
        row(
            2,
            "Colours:",
            ttk.Combobox(
                opt,
                textvariable=self.colours_var,
                values=["32", "64", "128", "256"],
                state="readonly",
                width=5,
            ),
            tk.Label(opt, text="Dither:"),
            ttk.Combobox(
                opt,
                textvariable=self.dither_var,
                values=list(DITHERS.values()),
                state="readonly",
                width=34,
            ),
        )
        self.loop_var = tk.StringVar(value="forever")
        self.limit_var = tk.StringVar(value="")
        row(
            3,
            "Loop:",
            ttk.Combobox(
                opt,
                textvariable=self.loop_var,
                values=["forever", "once", "twice", "3 times"],
                state="readonly",
                width=8,
            ),
            tk.Label(opt, text="Max size (MB, optional):"),
            tk.Entry(opt, textvariable=self.limit_var, width=6),
        )
        self.format_var = tk.StringVar(value="GIF")
        row(
            4,
            "Format:",
            ttk.Combobox(
                opt,
                textvariable=self.format_var,
                values=["GIF", "Animated WebP"],
                state="readonly",
                width=16,
            ),
        )

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            kind="image",
            ext=".gif",
            suggest=self._suggest,
        )
        self.out_row.pack(fill="x")
        self.size_lbl = tk.Label(out_lf, text="", fg=CLR["accent"])
        self.size_lbl.pack(anchor="w")
        self.btn_render = tk.Button(
            body,
            text=t("gif_maker.create_button"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
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

    def _suggest(self):
        src = self.src_row.path
        ext = ".webp" if self.format_var.get().startswith("Animated") else ".gif"
        return self.suggest_output(src, "anim", ext) if src else ""

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.preview.load(path, dur, 0.0)
        if not self.out_row.path:
            self.out_row.set(self._suggest())

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            start = ff.parse_time(self.start_var.get() or "0")
            dur = common.number(self.dur_var.get(), "Length", 0.1, 600)
            fps = common.number(self.fps_var.get(), "Frame rate", 1, 60)
            width = int(common.number(self.width_var.get(), "Width", 16, 4096)) // 2 * 2
            limit = (
                common.number(self.limit_var.get(), "Max size", 0.05, 500)
                if self.limit_var.get().strip()
                else 0
            )
            if start >= info.duration:
                raise ValueError("The start time is past the end of the clip.")
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        dur = min(dur, info.duration - start)
        webp = self.format_var.get().startswith("Animated")
        out = self.out_row.path or self._suggest()
        want = ".webp" if webp else ".gif"
        if not out.lower().endswith(want):
            out = os.path.splitext(out)[0] + want
        out = self.confirm_output(out, [src], default_ext=want)
        if not out:
            return
        self.out_row.set(out)
        dither = next((k for k, v in DITHERS.items() if v == self.dither_var.get()), "sierra2_4a")
        colours = int(self.colours_var.get())
        loops = {"forever": 0, "once": -1, "twice": 1, "3 times": 2}[self.loop_var.get()]
        cap = int(limit * 1024 * 1024) if limit else 0

        def work(_p, _c, ctx):
            def runner(cmd, duration, lo, hi):
                ctx.span(lo, hi).run(cmd, duration=duration)

            look = Animation(fps, width, colours, dither, loops, cap, webp)
            return make_animation(
                src, ctx.output(), look, Steps(runner, ctx.log), start=start, dur=dur
            )

        common.queue_job(
            self,
            common.Job(name="GIF", out=out, work=work, duration=dur, inputs=[src]),
            btn=self.btn_render,
            console=self.console,
        )
