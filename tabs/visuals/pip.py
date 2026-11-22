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
from dataclasses import dataclass
from tkinter import colorchooser, messagebox, ttk
from typing import Optional

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

POSITIONS = {
    "tl": "Top left", "tc": "Top centre", "tr": "Top right", "ml": "Middle left", "c": "Centre",
    "mr": "Middle right", "bl": "Bottom left", "bc": "Bottom centre", "br": "Bottom right", "custom": "Custom (%)",
}
ANIMS = {"none": "Just appear", "fade": "Fade in and out", "left": "Slide in from the left",
         "right": "Slide in from the right", "top": "Slide in from the top", "bottom": "Slide in from the bottom"}
AUDIO = {"bg": "Background's sound", "fg": "Inset's sound", "mix": "Both mixed", "none": "No sound"}


@dataclass
class PipSpec:
    bg_size: tuple
    fg_size: tuple
    pct: float = 30.0
    place: str = "br"
    custom: tuple = (50.0, 50.0)
    margin_pct: float = 3.0
    border: int = 0
    border_colour: str = "FFFFFF"
    radius: int = 0
    opacity: float = 1.0
    start: float = 0.0
    end: Optional[float] = None
    anim: str = "none"
    anim_dur: float = 0.5

    def inset_size(self) -> tuple:
        bw, _bh = self.bg_size
        fw, fh = self.fg_size
        w = max(16, int(bw * self.pct / 100) // 2 * 2)
        h = max(16, int(round(w * fh / max(1, fw) / 2)) * 2)
        return w, h

    def target_xy(self) -> tuple:
        bw, bh = self.bg_size
        w, h = self.inset_size()
        w, h = w + 2 * self.border, h + 2 * self.border
        m = int(min(bw, bh) * self.margin_pct / 100)
        if self.place == "custom":
            return int((bw - w) * self.custom[0] / 100), int((bh - h) * self.custom[1] / 100)
        col = {"l": m, "c": (bw - w) // 2, "r": bw - w - m}
        row = {"t": m, "m": (bh - h) // 2, "b": bh - h - m}
        key = {"c": "mc"}.get(self.place, self.place)
        return col[key[1]], row[key[0]]


def pip_graph(spec: PipSpec, bg: str = "[0:v]", fg: str = "[1:v]", out: str = "[v]") -> str:
    w, h = spec.inset_size()
    x, y = spec.target_xy()
    bw, bh = spec.bg_size
    fgc = [f"setpts=PTS-STARTPTS+{spec.start:.3f}/TB", f"scale={w}:{h}:flags=lanczos", "setsar=1", "format=yuva420p"]
    if spec.radius > 0:
        r = min(spec.radius, w // 2, h // 2)
        fgc.append(f"geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':"
                   f"a='255*lte(hypot(max(0,abs(X-W/2)-(W/2-{r})),max(0,abs(Y-H/2)-(H/2-{r}))),{r})'")
    if spec.border > 0:
        b = spec.border
        fgc.append(f"pad={w + 2 * b}:{h + 2 * b}:{b}:{b}:color=0x{spec.border_colour}")
    if spec.opacity < 0.999:
        fgc.append(f"colorchannelmixer=aa={spec.opacity:.3f}")
    end = spec.end
    d = max(0.05, spec.anim_dur)
    if spec.anim == "fade":
        fgc.append(f"fade=t=in:st={spec.start:.3f}:d={d:.2f}:alpha=1")
        if end is not None:
            fgc.append(f"fade=t=out:st={max(spec.start, end - d):.3f}:d={d:.2f}:alpha=1")
    xe, ye = str(x), str(y)
    if spec.anim in ("left", "right", "top", "bottom"):
        ow, oh = w + 2 * spec.border, h + 2 * spec.border
        p = f"min(1,max(0,(t-{spec.start:.3f})/{d:.3f}))"
        ease = f"(1-pow(1-{p},3))"                      # ease-out cubic
        if spec.anim == "left":
            xe = f"{-ow}+({x}+{ow})*{ease}"
        elif spec.anim == "right":
            xe = f"{bw}-({bw}-{x})*{ease}"
        elif spec.anim == "top":
            ye = f"{-oh}+({y}+{oh})*{ease}"
        else:
            ye = f"{bh}-({bh}-{y})*{ease}"
    enable = f"between(t,{spec.start:.3f},{end:.3f})" if end is not None else f"gte(t,{spec.start:.3f})"
    return (f"{fg}{','.join(fgc)}[pipfg];{bg}[pipfg]overlay=x='{xe}':y='{ye}':eof_action=pass:format=auto:"
            f"enable='{enable}',format=yuv420p{out}")


class PIPTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._bg = self._fg = None
        self.colour = "FFFFFF"
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.picture_in_picture"), "Put one video inside another.", icon="🖼")
        body = scroll_body(self)
        self.bg_row = FileRow(body, "Background video:", self.src_var, on_change=self._bg_changed, label_width=18)
        self.bg_row.pack(fill="x", padx=px(20), pady=px(10, 2))
        self.src_row = self.bg_row
        self.fg_row = FileRow(body, "Inset video:", on_change=self._fg_changed, label_width=18)
        self.fg_row.pack(fill="x", padx=px(20), pady=px(2))
        self.compare = CompareView(body, 400, 225, after_label="Result")
        self.compare.pack(pady=px(4))
        opt = tk.LabelFrame(body, text="  Inset  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))

        def ch(*_a):
            self._changed()

        self.size_var = tk.DoubleVar(value=30)
        tk.Scale(opt, variable=self.size_var, from_=5, to=90, orient="horizontal", length=px(180),
                 label="Width (% of background)", command=ch).grid(row=0, column=0, sticky="w")
        self.margin_var = tk.DoubleVar(value=3)
        tk.Scale(opt, variable=self.margin_var, from_=0, to=15, resolution=0.5, orient="horizontal", length=px(140),
                 label="Margin (%)", command=ch).grid(row=0, column=1, sticky="w")
        self.opacity_var = tk.DoubleVar(value=1.0)
        tk.Scale(opt, variable=self.opacity_var, from_=0.1, to=1.0, resolution=0.05, orient="horizontal",
                 length=px(140), label="Opacity", command=ch).grid(row=0, column=2, sticky="w")
        pr = tk.Frame(opt)
        pr.grid(row=1, column=0, columnspan=3, sticky="w", pady=px(3))
        tk.Label(pr, text="Position:").pack(side="left")
        self.place_var = tk.StringVar(value=POSITIONS["br"])
        pb = ttk.Combobox(pr, textvariable=self.place_var, values=list(POSITIONS.values()), state="readonly",
                          width=16)
        pb.pack(side="left", padx=px(4))
        pb.bind("<<ComboboxSelected>>", ch)
        self.cx_var, self.cy_var = tk.StringVar(value="50"), tk.StringVar(value="50")
        for label, var in (("X %", self.cx_var), ("Y %", self.cy_var)):
            tk.Label(pr, text=label).pack(side="left", padx=px(6, 2))
            tk.Entry(pr, textvariable=var, width=5).pack(side="left")
            var.trace_add("write", ch)
        br = tk.Frame(opt)
        br.grid(row=2, column=0, columnspan=3, sticky="w", pady=px(3))
        tk.Label(br, text="Border (px):").pack(side="left")
        self.border_var = tk.StringVar(value="0")
        tk.Entry(br, textvariable=self.border_var, width=4).pack(side="left", padx=px(4))
        self.colour_btn = tk.Button(br, text="Colour…", bg="#FFFFFF", relief="flat", command=self._pick_colour)
        self.colour_btn.pack(side="left")
        tk.Label(br, text="  Rounded corners (px):").pack(side="left")
        self.radius_var = tk.StringVar(value="0")
        tk.Entry(br, textvariable=self.radius_var, width=4).pack(side="left", padx=px(4))
        for v in (self.border_var, self.radius_var):
            v.trace_add("write", ch)
        tr = tk.Frame(opt)
        tr.grid(row=3, column=0, columnspan=3, sticky="w", pady=px(3))
        tk.Label(tr, text="Show from").pack(side="left")
        self.from_var, self.to_var = tk.StringVar(value="0"), tk.StringVar(value="")
        tk.Entry(tr, textvariable=self.from_var, width=9).pack(side="left", padx=px(2))
        tk.Label(tr, text="until").pack(side="left")
        tk.Entry(tr, textvariable=self.to_var, width=9).pack(side="left", padx=px(2))
        tk.Label(tr, text="(empty = until the inset ends)", fg=CLR["fgdim"]).pack(side="left")
        ar = tk.Frame(opt)
        ar.grid(row=4, column=0, columnspan=3, sticky="w", pady=px(3))
        self.anim_var = tk.StringVar(value=ANIMS["none"])
        ttk.Combobox(ar, textvariable=self.anim_var, values=list(ANIMS.values()), state="readonly",
                     width=22).pack(side="left")
        tk.Label(ar, text="over").pack(side="left", padx=px(4))
        self.anim_dur_var = tk.StringVar(value="0.5")
        tk.Entry(ar, textvariable=self.anim_dur_var, width=5).pack(side="left")
        tk.Label(ar, text="s    Sound:").pack(side="left")
        self.audio_var = tk.StringVar(value=AUDIO["bg"])
        ttk.Combobox(ar, textvariable=self.audio_var, values=list(AUDIO.values()), state="readonly",
                     width=20).pack(side="left", padx=px(4))

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.bg_row.path, "pip"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text="🖼  MAKE PICTURE-IN-PICTURE", font=(UI_FONT, 12, "bold"),
                                    bg=CLR["green"], fg="white", width=28, cursor="hand2", command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _pick_colour(self):
        res = colorchooser.askcolor(f"#{self.colour}", parent=self)
        if res and res[1]:
            self.colour = res[1].lstrip("#").upper()
            self.colour_btn.config(bg=res[1])
            self._changed()

    def _bg_changed(self, path):
        self._bg = ff.try_probe(path)
        self.compare.load(path)
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "pip"))
        self._changed()

    def _fg_changed(self, path):
        self._fg = ff.try_probe(path)
        self._changed()

    def spec(self) -> PipSpec:
        if self._bg is None or not self._bg.has_video or self._fg is None or not self._fg.has_video:
            raise ValueError("Choose a background video and an inset video.")
        start = ff.parse_time(self.from_var.get() or "0")
        end = ff.parse_time(self.to_var.get()) if self.to_var.get().strip() else None
        if end is not None and end <= start:
            raise ValueError("'until' must be after 'show from'.")
        if end is None and self._fg.duration:
            end = min(start + self._fg.duration, self._bg.duration or start + self._fg.duration)
        return PipSpec(
            bg_size=common.even_size(self._bg), fg_size=common.even_size(self._fg), pct=float(self.size_var.get()),
            place=next((k for k, v in POSITIONS.items() if v == self.place_var.get()), "br"),
            custom=(common.number(self.cx_var.get() or "50", "X %", 0, 100),
                    common.number(self.cy_var.get() or "50", "Y %", 0, 100)),
            margin_pct=float(self.margin_var.get()),
            border=int(common.number(self.border_var.get() or "0", "Border", 0, 100)),
            border_colour=self.colour, radius=int(common.number(self.radius_var.get() or "0", "Corners", 0, 500)),
            opacity=float(self.opacity_var.get()), start=start, end=end,
            anim=next((k for k, v in ANIMS.items() if v == self.anim_var.get()), "none"),
            anim_dur=common.number(self.anim_dur_var.get() or "0.5", "Animation length", 0.05, 10))

    def _changed(self, *_a):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(300, self._refresh)

    def _refresh(self):
        self._job = None
        try:
            spec = self.spec()
        except ValueError:
            return
        fg = fl.preview_movie(self.fg_row.path)
        g = pip_graph(spec, "[in]", fg, "")
        self.compare.set_filter(g, keep_time=True)

    def _render(self):
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
        graph = pip_graph(spec)
        mode = next((k for k, v in AUDIO.items() if v == self.audio_var.get()), "bg")
        dur = info.duration
        st = "aresample=48000,aformat=channel_layouts=stereo"
        if mode == "fg" and fginfo.has_audio:
            graph += (f";[1:a:0]{st},adelay=delays={round(spec.start * 1000)}:all=1,apad,"
                      f"atrim=duration={dur:.3f}[a]")
            audio = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        elif mode == "mix" and fginfo.has_audio and info.has_audio:
            graph += (f";[1:a:0]{st},adelay=delays={round(spec.start * 1000)}:all=1,apad,atrim=duration={dur:.3f}[fa];"
                      f"[0:a:0]{st}[ba];[ba][fa]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[a]")
            audio = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        elif mode == "none" or not info.has_audio:
            audio = ["-an"]
        else:
            audio = None                                  # background sound, kept as is when possible
        common.render(self, src=src, info=info, out=out, graph=graph, extra_inputs=["-i", fg],
                      encoder=self.encoder, console=self.console, btn=self.btn_render,
                      btn_label="🖼  MAKE PICTURE-IN-PICTURE", name="Picture-in-Picture", audio=audio,
                      extra_args=["-t", f"{dur:.3f}"], duration=dur, inputs=[fg])
