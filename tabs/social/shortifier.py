"""
tabs/social/shortifier.py  -  The Shortifier

One click from any video to the right shape for a platform: Shorts, TikTok,
Reels, square posts, widescreen... Fill the frame by cropping (choose which
part to keep), or fit the whole picture over a blurred, mirrored or solid
background.  Optional trim to the platform's length limit.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops.shortifier import ANCHORS, PLATFORMS, reframe_graph
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, SaveRow, scroll_body

BACKGROUNDS = {
    "crop": "Fill the frame (crop the edges)",
    "blur": "Fit + blurred background",
    "mirror": "Fit + mirrored background",
    "black": "Fit + black bars",
    "white": "Fit + white bars",
    "colour": "Fit + colour of your choice",
}


def platform_label(key: str) -> str:
    name, w, h, limit = PLATFORMS[key]
    if key == "custom":
        return name
    lim = f", max {limit // 60}:{limit % 60:02d}" if limit else ""
    return f"{name}  ({w}×{h}{lim})"


class ShortifierTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.colour = "1E1E1E"
        self._info = None
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.the_shortifier"),
            "Reformat any video for Shorts, TikTok, Reels, square posts and more.",
            icon="📱",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.compare = CompareView(body, 300, 300, after_label="Result")
        self.compare.pack(pady=px(4))

        opt = tk.LabelFrame(body, text="  Format  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))

        def row(r, label, *widgets):
            tk.Label(opt, text=label, width=12, anchor="w").grid(
                row=r, column=0, sticky="w", pady=px(2)
            )
            for i, w in enumerate(widgets):
                w.grid(row=r, column=1 + i, sticky="w", padx=px(0, 6), pady=px(2))

        self.platform_var = tk.StringVar(value=platform_label("shorts"))
        pb = ttk.Combobox(
            opt,
            textvariable=self.platform_var,
            values=[platform_label(k) for k in PLATFORMS],
            state="readonly",
            width=40,
        )
        pb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.cw_var, self.ch_var = tk.StringVar(value="1080"), tk.StringVar(value="1920")
        row(
            0,
            "Platform:",
            pb,
            tk.Label(opt, text="Custom:"),
            tk.Entry(opt, textvariable=self.cw_var, width=6),
            tk.Entry(opt, textvariable=self.ch_var, width=6),
        )
        self.bg_var = tk.StringVar(value=BACKGROUNDS["blur"])
        bb = ttk.Combobox(
            opt,
            textvariable=self.bg_var,
            values=list(BACKGROUNDS.values()),
            state="readonly",
            width=30,
        )
        bb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.colour_btn = tk.Button(
            opt,
            text="Colour…",
            relief="flat",
            bg=f"#{self.colour}",
            fg="white",
            command=self._pick_colour,
        )
        row(1, "Layout:", bb, self.colour_btn)
        self.anchor_var = tk.StringVar(value="Centre")
        ab = ttk.Combobox(
            opt,
            textvariable=self.anchor_var,
            values=[v[0] for v in ANCHORS.values()],
            state="readonly",
            width=12,
        )
        ab.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        row(
            2,
            "Keep:",
            ab,
            tk.Label(
                opt, text="(which part to keep when cropping)", fg=CLR["fgdim"], font=(UI_FONT, 8)
            ),
        )
        self.trim_var = tk.BooleanVar(value=True)
        row(
            3,
            "Length:",
            tk.Checkbutton(
                opt, text="Trim to the platform's maximum length", variable=self.trim_var
            ),
        )
        for var in (self.cw_var, self.ch_var):
            var.trace_add("write", lambda *_a: self._changed())

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"), show_ten_bit=False)
        self.encoder.pack(anchor="w")
        self.out_row = SaveRow(
            out_lf,
            t("common.output_file"),
            suggest=lambda: self.suggest_output(self.src_row.path, self._key()),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="📱  SHORTIFY",
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

    def _key(self):
        return next(
            (k for k in PLATFORMS if platform_label(k) == self.platform_var.get()), "shorts"
        )

    def size(self):
        key = self._key()
        if key == "custom":
            w = int(common.number(self.cw_var.get(), "Width", 16, 8192))
            h = int(common.number(self.ch_var.get(), "Height", 16, 8192))
            return w // 2 * 2, h // 2 * 2
        return PLATFORMS[key][1], PLATFORMS[key][2]

    def graph(self):
        tw, th = self.size()
        mode = next((k for k, v in BACKGROUNDS.items() if v == self.bg_var.get()), "blur")
        anchor = next((k for k, v in ANCHORS.items() if v[0] == self.anchor_var.get()), "center")
        return reframe_graph(tw, th, mode, anchor, self.colour)

    def _pick_colour(self):
        res = colorchooser.askcolor(f"#{self.colour}", parent=self)
        if res and res[1]:
            self.colour = res[1].lstrip("#").upper()
            self.colour_btn.config(bg=res[1])
            self.bg_var.set(BACKGROUNDS["colour"])
            self._changed()

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self.compare.load(path)
        self._changed()
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, self._key()))

    def _changed(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(200, self._refresh)

    def _refresh(self):
        self._job = None
        try:
            g = self.graph()
        except ValueError:
            return
        # the preview takes a -vf chain: drop the [0:v] / [v] labels
        self.compare.set_filter(g.replace("[0:v]", "").replace("[v]", ""))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            graph = self.graph()
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, self._key())
        if not out:
            return
        limit = PLATFORMS[self._key()][3]
        extra, dur = [], info.duration
        if self.trim_var.get() and limit and info.duration > limit:
            extra, dur = ["-t", str(limit)], float(limit)
            self.log(self.console, f"Trimming to the {limit}s limit.")
        tw, th = self.size()
        self.log(self.console, f"{PLATFORMS[self._key()][0]} · {tw}×{th}")
        common.render(
            self,
            common.RenderJob(
                src=src,
                info=info,
                out=out,
                graph=graph,
                name="Shortifier",
                extra_args=extra,
                duration=dur,
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
