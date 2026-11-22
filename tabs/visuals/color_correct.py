"""
tabs/visuals/color_correct.py  -  Colour Corrector

Exposure, contrast, tone (blacks / shadows / highlights / whites), white
balance, saturation, vibrance, hue and sharpening, with a live in-app
before/after preview of the exact filter chain that will be rendered.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

# key: (label, low, high, default, resolution)
CONTROLS = {
    "exposure": ("Exposure (stops)", -2.0, 2.0, 0.0, 0.05),
    "contrast": ("Contrast", 0.5, 2.0, 1.0, 0.01),
    "brightness": ("Brightness", -0.3, 0.3, 0.0, 0.01),
    "gamma": ("Gamma (midtones)", 0.4, 2.5, 1.0, 0.01),
    "blacks": ("Blacks", -0.2, 0.2, 0.0, 0.01),
    "shadows": ("Shadows", -1.0, 1.0, 0.0, 0.05),
    "highlights": ("Highlights", -1.0, 1.0, 0.0, 0.05),
    "whites": ("Whites", -0.2, 0.2, 0.0, 0.01),
    "temperature": ("Temperature (cool / warm)", -100.0, 100.0, 0.0, 1.0),
    "tint": ("Tint (green / magenta)", -100.0, 100.0, 0.0, 1.0),
    "saturation": ("Saturation", 0.0, 2.5, 1.0, 0.01),
    "vibrance": ("Vibrance", -1.0, 1.0, 0.0, 0.05),
    "hue": ("Hue rotate (degrees)", -180.0, 180.0, 0.0, 1.0),
    "sharpen": ("Sharpen", 0.0, 2.0, 0.0, 0.05),
}
DEFAULTS = {k: v[3] for k, v in CONTROLS.items()}
GROUPS = [
    ("Light", ("exposure", "contrast", "brightness", "gamma")),
    ("Tone", ("blacks", "shadows", "highlights", "whites")),
    ("Colour", ("temperature", "tint", "saturation", "vibrance", "hue")),
    ("Detail", ("sharpen",)),
]
LOOKS = {
    "Neutral (reset)": {},
    "Warm & sunny": {"temperature": 35, "saturation": 1.12, "vibrance": 0.2, "highlights": 0.15},
    "Cool & clean": {"temperature": -30, "contrast": 1.06, "whites": 0.03},
    "Punchy": {"contrast": 1.18, "vibrance": 0.45, "blacks": -0.03, "sharpen": 0.5},
    "Faded film": {
        "blacks": 0.08,
        "contrast": 0.9,
        "saturation": 0.8,
        "temperature": 12,
        "highlights": -0.2,
    },
    "Moody": {
        "exposure": -0.25,
        "contrast": 1.12,
        "saturation": 0.75,
        "temperature": -15,
        "shadows": -0.3,
    },
    "Black & white": {"saturation": 0.0, "contrast": 1.15},
    "Lift shadows": {"shadows": 0.45, "blacks": 0.02},
}


def _changed(values: dict, key: str) -> bool:
    return abs(float(values.get(key, DEFAULTS[key])) - DEFAULTS[key]) > 1e-6


def build_chain(values: dict) -> str:
    """FFmpeg filter chain for the slider *values* ('' when nothing changes)."""
    v = {k: float(values.get(k, d)) for k, d in DEFAULTS.items()}
    parts = []
    if _changed(v, "temperature"):
        kelvin = 6500 * 2 ** (-v["temperature"] / 100 * 0.6)
        parts.append(f"colortemperature=temperature={kelvin:.0f}:pl=1")
    if _changed(v, "tint"):
        parts.append(f"colorchannelmixer=gg={1 - v['tint'] / 100 * 0.15:.4f}")
    if _changed(v, "exposure"):
        gain = 2 ** v["exposure"]
        if gain > 1:
            m = f"{1 / gain:.4f}"
            parts.append(f"colorlevels=rimax={m}:gimax={m}:bimax={m}")
        else:
            m = f"{gain:.4f}"
            parts.append(f"colorlevels=romax={m}:gomax={m}:bomax={m}")
    eq = [
        f"{k}={v[k]:.3f}"
        for k in ("contrast", "brightness", "gamma", "saturation")
        if _changed(v, k)
    ]
    if eq:
        parts.append("eq=" + ":".join(eq))
    if any(_changed(v, k) for k in ("blacks", "shadows", "highlights", "whites")):
        b, w = v["blacks"], v["whites"]
        lo = f"0/{b:.3f}" if b >= 0 else f"{-b:.3f}/0"
        hi = f"1/{1 + w:.3f}" if w <= 0 else f"{1 - w:.3f}/1"
        ys = min(max(0.25 + 0.12 * v["shadows"], 0.02), 0.6)
        yh = min(max(0.75 + 0.12 * v["highlights"], 0.4), 0.98)
        parts.append(f"curves=all='{lo} 0.25/{ys:.3f} 0.75/{yh:.3f} {hi}':interp=pchip")
    if _changed(v, "vibrance"):
        parts.append(f"vibrance=intensity={v['vibrance']:.3f}")
    if _changed(v, "hue"):
        parts.append(f"hue=h={v['hue']:.1f}")
    if _changed(v, "sharpen"):
        parts.append(f"unsharp=5:5:{v['sharpen']:.2f}:5:5:0")
    return ",".join(parts)


class ColorCorrectorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.vars: dict[str, tk.DoubleVar] = {}
        self._preview_job = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.basic_color_corrector"),
            "Exposure, tone, white balance and colour with a live before/after preview.",
            icon="🌈",
        )
        body = scroll_body(self)

        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        self.compare = CompareView(body, 400, 225)
        self.compare.pack(pady=px(6))

        looks = tk.Frame(body, bg=CLR["bg"])
        looks.pack(fill="x", padx=px(20), pady=px(2, 4))
        tk.Label(
            looks, text="Quick look:", bg=CLR["bg"], fg=CLR["fg"], font=(UI_FONT, 10, "bold")
        ).pack(side="left")
        self.look_var = tk.StringVar(value="Neutral (reset)")
        box = ttk.Combobox(
            looks, textvariable=self.look_var, values=list(LOOKS), state="readonly", width=22
        )
        box.pack(side="left", padx=px(8))
        box.bind("<<ComboboxSelected>>", lambda _e: self._apply_look(self.look_var.get()))
        tk.Button(
            looks,
            text=t("color_correct.btn_reset_all"),
            relief="flat",
            cursor="hand2",
            command=lambda: self._apply_look("Neutral (reset)"),
        ).pack(side="left", padx=px(6))

        grid = tk.Frame(body, bg=CLR["bg"])
        grid.pack(fill="x", padx=px(20), pady=px(4))
        for col, (title, keys) in enumerate((GROUPS[0], GROUPS[2])):
            self._group(grid, title, keys).grid(
                row=0, column=col, sticky="nsew", padx=px(4), pady=px(4)
            )
        for col, (title, keys) in enumerate((GROUPS[1], GROUPS[3])):
            self._group(grid, title, keys).grid(
                row=1, column=col, sticky="nsew", padx=px(4), pady=px(4)
            )
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        enc_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        enc_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(
            enc_lf, codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr")
        )
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(
            enc_lf,
            t("common.output_file"),
            save=True,
            suggest=lambda: self.suggest_output(self.src_row.path, "graded"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))

        self.btn_render = tk.Button(
            body,
            text=t("color_correct.render_grade"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["pink"],
            fg="white",
            width=24,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body, bg=CLR["bg"])
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _group(self, parent, title, keys):
        lf = tk.LabelFrame(parent, text=f"  {title}  ", padx=px(10), pady=px(4))
        for row, key in enumerate(keys):
            label, lo, hi, default, res = CONTROLS[key]
            var = tk.DoubleVar(value=default)
            self.vars[key] = var
            tk.Label(lf, text=label, anchor="w", width=22).grid(row=row, column=0, sticky="w")
            tk.Scale(
                lf,
                variable=var,
                from_=lo,
                to=hi,
                resolution=res,
                orient="horizontal",
                length=px(190),
                showvalue=True,
                command=lambda _v: self._schedule_preview(),
            ).grid(row=row, column=1)
            tk.Button(
                lf,
                text="↺",
                width=2,
                relief="flat",
                cursor="hand2",
                command=lambda v=var, d=default: (v.set(d), self._schedule_preview()),
            ).grid(row=row, column=2, padx=px(2))
        return lf

    # ── Behaviour ────────────────────────────────────────────────────────────

    def values(self) -> dict:
        return {k: v.get() for k, v in self.vars.items()}

    def _apply_look(self, name):
        merged = dict(DEFAULTS)
        merged.update(LOOKS.get(name, {}))
        for key, var in self.vars.items():
            var.set(merged[key])
        self._schedule_preview()

    def _source_changed(self, path):
        self.compare.load(path)
        self.compare.set_filter(build_chain(self.values()))
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "graded"))

    def _schedule_preview(self):
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(150, self._update_preview)

    def _update_preview(self):
        self._preview_job = None
        self.compare.set_filter(build_chain(self.values()))

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        chain = build_chain(self.values())
        if not chain:
            self.log(self.console, "No adjustments set - the video will just be re-encoded.")
        out = common.output_for(self, self.out_row, src, "graded")
        if not out:
            return
        common.render(
            self,
            src=src,
            info=info,
            out=out,
            chain=chain,
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            btn_label=t("color_correct.render_grade"),
            name="Colour Correct",
        )
