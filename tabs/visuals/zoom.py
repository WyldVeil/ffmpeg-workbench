"""
tabs/visuals/zoom.py  -  Animated Zoom & Pan

Punch-in zooms, Ken Burns moves, keyframed pans and zoom pulses.  The zoom
and the point to keep in view are expressions of time, so the clip keeps
its length and frame rate:

* GPU (Vulkan, libplacebo): sub-pixel crop - perfectly smooth motion.
* CPU: zoompan, one output frame per input frame, on a 2x supersampled
  picture (for sources up to 1080p) so slow moves don't jitter.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, ClassVar

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.ops.shared import frame_rate
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, FramePreview, here_button, scroll_body

MODES = {
    "punch": "Punch-in (zoom in at a moment, then back out)",
    "kenburns": "Ken Burns (slow move over the clip)",
    "keyframes": "Keyframe pan (from one framing to another)",
    "pulse": "Zoom pulse (quick bump)",
}
KB = {
    "in": "Zoom in",
    "out": "Zoom out",
    "lr": "Pan left → right",
    "rl": "Pan right → left",
    "tb": "Pan top → bottom",
    "bt": "Pan bottom → top",
}
EASES = {"smooth": "Smooth", "snappy": "Snappy", "linear": "Linear"}


def _progress(a: float, b: float) -> str:
    return f"clip((T-{a:.3f})/{max(b - a, 0.001):.3f},0,1)"


def _ease(p: str, kind: str) -> str:
    if kind == "linear":
        return p
    if kind == "snappy":
        return f"(1-pow(1-{p},3))"
    return f"({p}*{p}*(3-2*{p}))"


def zoom_exprs(mode: str, p: dict, duration: float) -> tuple:
    """(zoom, focus_x, focus_y) expressions in T (seconds); focus as 0..1 of the frame."""
    ease = p.get("ease", "smooth")
    if mode == "punch":
        ramp = 0.18 if ease == "snappy" else 0.5
        a, b = p["start"], max(p["end"], p["start"] + 2 * ramp)
        e = f"({_ease(_progress(a, a + ramp), ease)}-{_ease(_progress(b - ramp, b), ease)})"
        return f"1+{p['zoom'] - 1:.4f}*{e}", f"{p['x'] / 100:.4f}", f"{p['y'] / 100:.4f}"
    if mode == "pulse":
        e = f"sin(PI*{_progress(p['time'], p['time'] + p['dur'])})"
        return f"1+{p['zoom'] - 1:.4f}*{e}", f"{p['x'] / 100:.4f}", f"{p['y'] / 100:.4f}"
    if mode == "kenburns":
        e = _ease(_progress(0, duration), ease)
        z = p["zoom"]
        d = p["dir"]
        if d == "in":
            return f"1+{z - 1:.4f}*{e}", "0.5", "0.5"
        if d == "out":
            return f"{z:.4f}-{z - 1:.4f}*{e}", "0.5", "0.5"
        if d in ("lr", "rl"):
            return f"{z:.4f}", (f"({e})" if d == "lr" else f"(1-{e})"), "0.5"
        return f"{z:.4f}", "0.5", (f"({e})" if d == "tb" else f"(1-{e})")
    # keyframes
    e = _ease(_progress(p["t0"], p["t1"]), ease)
    (x0, y0, z0), (x1, y1, z1) = p["from"], p["to"]
    return (
        f"{z0:.4f}+{z1 - z0:.4f}*{e}",
        f"{x0 / 100:.4f}+{(x1 - x0) / 100:.4f}*{e}",
        f"{y0 / 100:.4f}+{(y1 - y0) / 100:.4f}*{e}",
    )


def zoom_filter(
    exprs: tuple,
    size: tuple,
    fps: str,
    *,
    gpu: bool,
    pix_fmt: str = "yuv420p",
    supersample: bool = True,
) -> str:
    z, fx, fy = exprs
    w, h = size
    if gpu:
        z, fx, fy = (e.replace("T", "t") for e in (z, fx, fy))
        return (
            f"libplacebo=w={w}:h={h}:crop_w='iw/max(1,{z})':crop_h='ih/max(1,{z})':"
            f"crop_x='clip(({fx})*iw-crop_w/2,0,iw-crop_w)':crop_y='clip(({fy})*ih-crop_h/2,0,ih-crop_h)':"
            f"format={pix_fmt}"
        )
    z, fx, fy = (e.replace("T", "it") for e in (z, fx, fy))
    pre = "scale=iw*2:ih*2:flags=bicubic," if supersample else ""
    return (
        f"{pre}zoompan=z='max(1,{z})':x='clip(({fx})*iw-iw/zoom/2,0,iw-iw/zoom)':"
        f"y='clip(({fy})*ih-ih/zoom/2,0,ih-ih/zoom)':d=1:s={w}x{h}:fps={fps},setsar=1"
    )


class AnimatedZoomTab(BaseTab):
    # i / o: the move starts / ends at the frame shown (for the chosen kind of move)
    _MARKS: ClassVar[dict[str, tuple[str, str | None]]] = {
        "punch": ("p_start", "p_end"),
        "keyframes": ("k_t0", "k_t1"),
        "pulse": ("u_time", None),
    }

    def _mark(self, which):
        key = next((k for k, v in MODES.items() if v == self.mode_var.get()), "")
        name = self._MARKS.get(key, (None, None))[which]
        if name:
            self.vars[name].set(ff.format_time(float(self.pos_var.get())))

    def mark_in(self):
        self._mark(0)

    def mark_out(self):
        self._mark(1)

    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self, t("tab.animated_zoom"), "Punch-ins, Ken Burns moves and smooth pans.", icon="🔍"
        )
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
        self.slider = ttk.Scale(
            pv,
            from_=0,
            to=1,
            variable=self.pos_var,
            length=px(400),
            command=lambda _v: self.preview.show(float(self.pos_var.get())),
        )
        self.slider.pack(fill="x")
        framestep.slider(self.slider, self.preview)  # , and . step a frame
        tk.Label(
            pv,
            text="Move the slider to see the zoom at any moment.",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack()

        opt = tk.LabelFrame(body, text="  Move  ", padx=px(12), pady=px(8))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        self.mode_var = tk.StringVar(value=MODES["punch"])
        mb = ttk.Combobox(
            opt, textvariable=self.mode_var, values=list(MODES.values()), state="readonly", width=46
        )
        mb.pack(anchor="w")
        mb.bind("<<ComboboxSelected>>", lambda _e: self._mode_changed())
        self.frames = {}
        v = self.vars = {}

        def entry(parent, key, default, width=6):
            v[key] = tk.StringVar(value=default)
            v[key].trace_add("write", lambda *_a: self._changed())
            return tk.Entry(parent, textvariable=v[key], width=width)

        def time_entry(parent, key, default, width=8):
            box = tk.Frame(parent)
            entry(box, key, default, width).pack(side="left")
            here_button(box, v[key], lambda: float(self.pos_var.get())).pack(side="left")
            return box

        def labelled(parent, *items):
            row = tk.Frame(parent)
            row.pack(anchor="w", pady=px(2))
            for item in items:
                (tk.Label(row, text=item) if isinstance(item, str) else item(row)).pack(
                    side="left", padx=px(2)
                )

        f = self.frames["punch"] = tk.Frame(opt)
        labelled(
            f,
            "From",
            lambda r: time_entry(r, "p_start", "1"),
            "to",
            lambda r: time_entry(r, "p_end", "3"),
            "  zoom ×",
            lambda r: entry(r, "p_zoom", "1.5", 5),
        )
        labelled(
            f,
            "Keep in view - X %",
            lambda r: entry(r, "p_x", "50", 5),
            "Y %",
            lambda r: entry(r, "p_y", "40", 5),
        )
        f = self.frames["kenburns"] = tk.Frame(opt)
        self.kb_var = tk.StringVar(value=KB["in"])
        kb = ttk.Combobox(
            f, textvariable=self.kb_var, values=list(KB.values()), state="readonly", width=20
        )
        kb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        labelled(f, lambda r: kb, "  zoom ×", lambda r: entry(r, "kb_zoom", "1.3", 5))
        f = self.frames["keyframes"] = tk.Frame(opt)
        labelled(
            f,
            "Start: X %",
            lambda r: entry(r, "k_x0", "50", 5),
            "Y %",
            lambda r: entry(r, "k_y0", "50", 5),
            "zoom ×",
            lambda r: entry(r, "k_z0", "1.0", 5),
            "  at",
            lambda r: time_entry(r, "k_t0", "0"),
        )
        labelled(
            f,
            "End:   X %",
            lambda r: entry(r, "k_x1", "70", 5),
            "Y %",
            lambda r: entry(r, "k_y1", "35", 5),
            "zoom ×",
            lambda r: entry(r, "k_z1", "1.6", 5),
            "  at",
            lambda r: time_entry(r, "k_t1", ""),
            "(empty = the end)",
        )
        f = self.frames["pulse"] = tk.Frame(opt)
        labelled(
            f,
            "At",
            lambda r: time_entry(r, "u_time", "1"),
            "lasting",
            lambda r: entry(r, "u_dur", "0.4", 5),
            "s   zoom ×",
            lambda r: entry(r, "u_zoom", "1.3", 5),
        )
        labelled(
            f,
            "Keep in view - X %",
            lambda r: entry(r, "u_x", "50", 5),
            "Y %",
            lambda r: entry(r, "u_y", "50", 5),
        )
        er = self._ease_row = tk.Frame(opt)
        er.pack(anchor="w", pady=px(4))
        tk.Label(er, text="Motion:").pack(side="left")
        self.ease_var = tk.StringVar(value=EASES["smooth"])
        eb = ttk.Combobox(
            er, textvariable=self.ease_var, values=list(EASES.values()), state="readonly", width=10
        )
        eb.pack(side="left", padx=px(4))
        eb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        tk.Label(er, text="GPU (smoothest)" if fl.vulkan_ok() else "CPU", fg=CLR["fgdim"]).pack(
            side="left", padx=px(10)
        )

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(
            out_lf,
            t("common.output_file"),
            save=True,
            suggest=lambda: self.suggest_output(self.src_row.path, "zoom"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(
            body,
            text="🔍  RENDER ZOOM",
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
        self._mode_changed()

    def _mode(self):
        return next((k for k, v in MODES.items() if v == self.mode_var.get()), "punch")

    def _mode_changed(self):
        for key, frame in self.frames.items():
            if key == self._mode():
                frame.pack(anchor="w", pady=px(4), before=self._ease_row)
            else:
                frame.pack_forget()
        self._changed()

    def params(self, duration: float) -> dict[str, Any]:
        v = {k: var.get().strip() for k, var in self.vars.items()}
        p: dict[str, Any]
        num = common.number
        ease = next((k for k, x in EASES.items() if x == self.ease_var.get()), "smooth")
        mode = self._mode()
        if mode == "punch":
            p = {
                "start": ff.parse_time(v["p_start"] or "0"),
                "end": ff.parse_time(v["p_end"] or "0"),
                "zoom": num(v["p_zoom"], "Zoom", 1.01, 10),
                "x": num(v["p_x"], "X %", 0, 100),
                "y": num(v["p_y"], "Y %", 0, 100),
            }
            if p["end"] <= p["start"]:
                raise ValueError("The zoom must end after it starts.")
        elif mode == "pulse":
            p = {
                "time": ff.parse_time(v["u_time"] or "0"),
                "dur": num(v["u_dur"], "Length", 0.05, 10),
                "zoom": num(v["u_zoom"], "Zoom", 1.01, 10),
                "x": num(v["u_x"], "X %", 0, 100),
                "y": num(v["u_y"], "Y %", 0, 100),
            }
        elif mode == "kenburns":
            p = {
                "dir": next((k for k, x in KB.items() if x == self.kb_var.get()), "in"),
                "zoom": num(v["kb_zoom"], "Zoom", 1.01, 10),
            }
        else:
            t1 = ff.parse_time(v["k_t1"]) if v["k_t1"] else duration
            p = {
                "from": (
                    num(v["k_x0"], "X %", 0, 100),
                    num(v["k_y0"], "Y %", 0, 100),
                    num(v["k_z0"], "Zoom", 1, 10),
                ),
                "to": (
                    num(v["k_x1"], "X %", 0, 100),
                    num(v["k_y1"], "Y %", 0, 100),
                    num(v["k_z1"], "Zoom", 1, 10),
                ),
                "t0": ff.parse_time(v["k_t0"] or "0"),
                "t1": t1,
            }
            if p["t1"] <= p["t0"]:
                raise ValueError("The end time must be after the start time.")
        p["ease"] = ease
        return p

    def _source_changed(self, path):
        self._info = info = ff.try_probe(path)
        dur = info.duration if info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.preview.load(path, dur, min(1.0, dur / 2))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "zoom"))
        self._changed()

    def _changed(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(300, self._refresh)

    def _refresh(self):
        self._job = None
        info = self._info
        if info is None or not info.has_video:
            return
        try:
            exprs = zoom_exprs(self._mode(), self.params(info.duration), info.duration)
        except ValueError:
            return
        self.preview.set_filter(
            zoom_filter(
                exprs, common.even_size(info), frame_rate(info), gpu=False, supersample=False
            ),
            keep_time=True,
        )

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            exprs = zoom_exprs(self._mode(), self.params(info.duration), info.duration)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "zoom")
        if not out:
            return
        size = common.even_size(info)
        gpu = fl.vulkan_ok()
        chain = zoom_filter(
            exprs,
            size,
            frame_rate(info),
            gpu=gpu,
            pix_fmt="yuv420p10le" if self.encoder.ten_bit else "yuv420p",
            supersample=max(size) <= 1920,
        )
        common.render(
            self,
            src=src,
            info=info,
            out=out,
            chain=chain,
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            name="Animated Zoom",
            pre_input=["-init_hw_device", "vulkan"] if gpu else (),
        )
