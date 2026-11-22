"""
tabs/visuals/scopes.py  -  Video Scopes

Live waveform, RGB parade, histogram, vectorscope and false-colour views of
any frame (scrub with the slider), PNG snapshots, and export of a "scope
video" with the scope beside or below the picture, or a 2x2 grid of all
four scopes for colourist-style review.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, FramePreview, scroll_body

# key: (label, filter chain, display aspect (w/h) or None = same as video, help)
SCOPES = {
    "waveform": ("Waveform (luma)",
                 "waveform=m=column:c=1:d=overlay:s=ire:g=green:fl=numbers+dots:i=0.08",
                 16 / 9, "Brightness across the frame. IRE scale: 0 = black, 100 = white; "
                         "anything flat at the top or bottom is clipped."),
    "parade": ("RGB parade",
               "format=gbrp,waveform=m=column:c=7:d=parade:s=ire:g=green:fl=numbers+dots:i=0.08",
               16 / 9, "Red, green and blue waveforms side by side - level the three for a "
                       "neutral white balance."),
    "histogram": ("Histogram (RGB)",
                  "format=gbrp,histogram=display_mode=stack:levels_mode=linear",
                  4 / 3, "How many pixels sit at each level. Piled against an edge = clipping."),
    "vectorscope": ("Vectorscope",
                    "vectorscope=m=color2:g=color:f=name:c=auto",
                    1.0, "Hue (angle) and saturation (distance from centre); the targets mark "
                         "the 75% colour bars. Skin tones sit near the line to the top left."),
    "falsecolor": ("False colour (exposure)", "pseudocolor=p=turbo", None,
                   "Exposure map: blue = shadows, green = mid-tones, red = highlights."),
}
GRID = ("waveform", "parade", "vectorscope", "histogram")
LAYOUTS = {
    "side": "Scope to the right of the picture",
    "below": "Scope below the picture",
    "grid": "Picture + all four scopes (2×2 grid)",
    "only": "Scope only",
}


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def scope_box(key: str, width: int, height: int, *, fit: str = "height") -> tuple[int, int]:
    """Size for scope *key* next to a WxH picture (fit='height') or under it ('width')."""
    aspect = SCOPES[key][2] or (width / height)
    if fit == "width":
        return _even(width), _even(width / aspect)
    return _even(height * aspect), _even(height)


def export_graph(key: str, layout: str, width: int, height: int) -> str:
    """filter_complex graph (output [v]) for a scope video."""
    def chain(k, bw, bh):
        return f"{SCOPES[k][1]},scale={bw}:{bh},setsar=1,format=yuv420p"

    if layout == "grid":
        hw, hh = _even(width / 2), _even(height / 2)
        parts = ["[0:v]setsar=1,format=yuv420p,split=5[src][s0][s1][s2][s3]"]
        for i, k in enumerate(GRID):
            parts.append(f"[s{i}]{chain(k, hw, hh)}[g{i}]")
        parts.append("[g0][g1][g2][g3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[grid]")
        parts.append(f"[src]scale={2 * hw}:{2 * hh}[pic]")
        parts.append("[pic][grid]hstack=inputs=2[v]")
        return ";".join(parts)
    if layout == "only":
        bw, bh = scope_box(key, width, height)
        return f"[0:v]{chain(key, bw, bh)}[v]"
    fit = "width" if layout == "below" else "height"
    bw, bh = scope_box(key, width, height, fit=fit)
    stack = "vstack" if layout == "below" else "hstack"
    return (f"[0:v]setsar=1,format=yuv420p,split[src][s];[s]{chain(key, bw, bh)}[sc];"
            f"[src][sc]{stack}=inputs=2[v]")


class VideoScopesTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.video_scopes"), t("scopes.subtitle"), icon="📊")
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_video"), self.src_var,
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        pick = tk.Frame(body, bg=CLR["bg"])
        pick.pack(fill="x", padx=px(20), pady=px(4))
        tk.Label(pick, text="Scope:", bg=CLR["bg"], fg=CLR["fg"],
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.scope_var = tk.StringVar(value="waveform")
        for key, (label, *_rest) in SCOPES.items():
            tk.Radiobutton(pick, text=label, value=key, variable=self.scope_var,
                           bg=CLR["bg"], fg=CLR["fg"], selectcolor=CLR["input_bg"],
                           activebackground=CLR["bg"],
                           command=self._scope_changed).pack(side="left", padx=px(4))
        self.help_lbl = tk.Label(body, text="", bg=CLR["bg"], fg=CLR["fgdim"],
                                 font=(UI_FONT, 9), wraplength=px(820), justify="left")
        self.help_lbl.pack(anchor="w", padx=px(24))

        views = tk.Frame(body, bg=CLR["bg"])
        views.pack(pady=px(6))
        self.picture = FramePreview(views, 400, 225, show_time=False)
        self.picture.pack(side="left", padx=px(4))
        self.scope_view = FramePreview(views, 400, 225, show_time=False)
        self.scope_view.pack(side="left", padx=px(4))
        bar = tk.Frame(body, bg=CLR["bg"])
        bar.pack(fill="x", padx=px(24))
        self.pos_var = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(bar, from_=0.0, to=1.0, variable=self.pos_var,
                                command=lambda _v: self._moved())
        self.slider.pack(side="left", fill="x", expand=True)
        framestep.slider(self.slider, lambda: framestep.fps_of(self.src_row.path))
        self.time_lbl = tk.Label(bar, text="", width=12, bg=CLR["bg"], fg=CLR["fgdim"])
        self.time_lbl.pack(side="left", padx=px(6))
        tk.Button(bar, text="Save snapshot…", relief="flat", cursor="hand2",
                  command=self._snapshot).pack(side="left")

        vid = tk.LabelFrame(body, text=f"  {t('scopes.scope_video_section')}  ", padx=px(12), pady=px(8))
        vid.pack(fill="x", padx=px(20), pady=px(8))
        row = tk.Frame(vid)
        row.pack(fill="x", pady=px(2))
        tk.Label(row, text=t("scopes.layout_label"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.layout_var = tk.StringVar(value=LAYOUTS["side"])
        ttk.Combobox(row, textvariable=self.layout_var, values=list(LAYOUTS.values()),
                     state="readonly", width=38).pack(side="left", padx=px(8))
        self.encoder = EncoderPanel(vid, codecs=("h264", "hevc", "prores"), show_ten_bit=False)
        self.encoder.pack(anchor="w", pady=px(6, 0))
        self.out_row = FileRow(vid, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "scopes"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_vid = tk.Button(vid, text=t("scopes.scope_video_button"), bg="#7B1FA2",
                                 fg="white", font=(UI_FONT, 10, "bold"), cursor="hand2",
                                 command=self._render_video)
        self.btn_vid.pack(pady=px(6))

        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._scope_changed()

    # ── Behaviour ────────────────────────────────────────────────────────────

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        dur = self._info.duration if self._info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.pos_var.set(dur * 0.25)
        self.picture.load(path, dur, dur * 0.25)
        self.scope_view.set_filter(SCOPES[self.scope_var.get()][1])
        self.scope_view.load(path, dur, dur * 0.25)
        self.time_lbl.config(text=ff.format_time(dur * 0.25))

    def _scope_changed(self):
        key = self.scope_var.get()
        self.help_lbl.config(text="ℹ  " + SCOPES[key][3])
        self.scope_view.set_filter(SCOPES[key][1])

    def _moved(self):
        pos = float(self.pos_var.get())
        self.time_lbl.config(text=ff.format_time(pos))
        self.picture.show(pos)
        self.scope_view.show(pos)

    def _layout_key(self) -> str:
        label = self.layout_var.get()
        return next((k for k, v in LAYOUTS.items() if v == label), "side")

    def _snapshot(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        from core import dialogs
        base = os.path.splitext(os.path.basename(src))[0]
        out = dialogs.ask_save(f"{base}_{self.scope_var.get()}.png", kind="image",
                               defaultextension=".png", parent=self)
        if not out:
            return
        w, h = common.even_size(info)
        layout = "grid" if self._layout_key() == "grid" else "side"
        graph = export_graph(self.scope_var.get(), layout, w, h)
        cmd = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
               "-ss", ff.ts(float(self.pos_var.get())), "-i", src, "-filter_complex", graph,
               "-map", "[v]", "-frames:v", "1", "-update", "1", out]

        def work():
            return ff.capture(cmd, timeout=120)

        def done(res):
            if res.returncode == 0 and os.path.isfile(out):
                self.log_tagged(self.console, f"Snapshot saved: {out}", "success")
            else:
                self.log_tagged(self.console, "Snapshot failed: " + ff.summarize_error(
                    (res.stderr or "").splitlines(), res.returncode), "error")

        from core import ui
        ui.run_in_thread(work, on_done=done, name="ScopeSnapshot")

    def _render_video(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        w, h = common.even_size(info)
        out = common.output_for(self, self.out_row, src, "scopes")
        if not out:
            return
        graph = export_graph(self.scope_var.get(), self._layout_key(), w, h)
        common.render(self, src=src, info=info, out=out, graph=graph, encoder=self.encoder,
                      console=self.console, btn=self.btn_vid,
                      btn_label=t("scopes.scope_video_button"), name="Scope video")
