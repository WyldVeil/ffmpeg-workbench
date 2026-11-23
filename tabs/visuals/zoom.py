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
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any, ClassVar

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.ops import zoom as zm
from core.ops.shared import RenderJob, frame_rate
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.kit.time_fields import MomentPicker
from tabs.kit.tktyped import on_write
from tabs.widgets import FileRow, FramePreview, output_section, scroll_body

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


class AnimatedZoomTab(BaseTab):
    # i / o: the move starts / ends at the frame shown (for the chosen kind of move)
    _MARKS: ClassVar[dict[str, tuple[str, str | None]]] = {
        "punch": ("p_start", "p_end"),
        "keyframes": ("k_t0", "k_t1"),
        "pulse": ("u_time", None),
    }

    def _mark(self, which: int) -> None:
        key = next((k for k, v in MODES.items() if v == self.mode_var.get()), "")
        name = self._MARKS.get(key, (None, None))[which]
        if name:
            self.vars[name].set(ff.format_time(float(self.pos_var.get())))

    def mark_in(self) -> None:
        self._mark(0)

    def mark_out(self) -> None:
        self._mark(1)

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self._job: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        body, opt = self._build_header()
        self._build_moves(opt)
        self._build_output(body, opt)

    def _build_header(self) -> tuple[tk.Frame, tk.LabelFrame]:
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
        self.frames: dict[str, tk.Frame] = {}
        self.vars: dict[str, tk.StringVar] = {}
        return body, opt

    def _entry(self, parent: tk.Misc, key: str, default: str, width: int = 6) -> tk.Entry:
        """A number box whose text is ``self.vars[key]``."""
        self.vars[key] = var = tk.StringVar(value=default)
        on_write(var, lambda: self._changed())
        return tk.Entry(parent, textvariable=var, width=width)

    def _time_entry(self, parent: tk.Misc, key: str, default: str, width: int = 8) -> MomentPicker:
        """A time box (with the frame picker) whose text is ``self.vars[key]``."""
        picker = MomentPicker(
            parent,
            value=default,
            width=width,
            here=lambda: float(self.pos_var.get()),
            on_change=self._changed,
        )
        self.vars[key] = picker.var
        return picker

    @staticmethod
    def _labelled(parent: tk.Misc, *items: str | Callable[[tk.Misc], tk.Widget]) -> None:
        """A row of words and boxes: each item is text, or makes its widget in the row."""
        row = tk.Frame(parent)
        row.pack(anchor="w", pady=px(2))
        for item in items:
            (tk.Label(row, text=item) if isinstance(item, str) else item(row)).pack(
                side="left", padx=px(2)
            )

    def _build_moves(self, opt: tk.LabelFrame) -> None:
        """The settings of each kind of move, one frame per kind (shown by mode)."""
        entry, time_entry, labelled = self._entry, self._time_entry, self._labelled
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

    def _build_output(self, body: tk.Frame, opt: tk.LabelFrame) -> None:
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

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9"),
            suggest=lambda: self.suggest_output(self.src_row.path, "zoom"),
        )
        self.btn_render = self.make_render_btn(body, "🔍  RENDER ZOOM", self._render, width=22)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)
        self._mode_changed()

    def _mode(self) -> str:
        return next((k for k, v in MODES.items() if v == self.mode_var.get()), "punch")

    def _mode_changed(self) -> None:
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

    def _source_changed(self, path: str) -> None:
        self._info = info = ff.try_probe(path)
        dur = info.duration if info else 0.0
        self.slider.configure(to=max(dur, 0.01))
        self.preview.load(path, dur, min(1.0, dur / 2))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "zoom"))
        self._changed()

    def _changed(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(300, self._refresh)

    def _refresh(self) -> None:
        self._job = None
        info = self._info
        if info is None or not info.has_video:
            return
        try:
            exprs = zm.zoom_exprs(self._mode(), self.params(info.duration), info.duration)
        except ValueError:
            return
        self.preview.set_filter(
            zm.zoom_filter(
                exprs, common.even_size(info), frame_rate(info), gpu=False, supersample=False
            ),
            keep_time=True,
        )

    def _render(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            exprs = zm.zoom_exprs(self._mode(), self.params(info.duration), info.duration)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "zoom")
        if not out:
            return
        size = common.even_size(info)
        gpu = fl.vulkan_ok()
        chain = zm.zoom_filter(
            exprs,
            size,
            frame_rate(info),
            gpu=gpu,
            pix_fmt="yuv420p10le" if self.encoder.ten_bit else "yuv420p",
            supersample=max(size) <= 1920,
        )
        common.render(
            self,
            RenderJob(
                src=src,
                info=info,
                out=out,
                chain=chain,
                name="Animated Zoom",
                pre_input=ff.VULKAN_DEVICE if gpu else (),
            ),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
