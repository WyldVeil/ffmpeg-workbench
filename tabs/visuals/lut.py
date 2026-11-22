"""
tabs/visuals/lut.py  -  LUT Applicator

Apply a colour look-up table: 3D .cube/.3dl/.dat/.m3d/.csp files, 1D .cube
files, Hald CLUT images (.png/.tif), or one of the built-in looks.  An
intensity slider blends the look with the original, and the before/after
preview shows exactly what will be rendered.
"""
from __future__ import annotations

import math
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core.i18n import t
from core.ops.lut import INTERPOLATIONS, build_chain
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

_CUSTOM = "Use a LUT file…"


# ── Built-in looks (generated .cube files, no licensing strings attached) ────

def _clip(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _luma(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _smooth(e0, e1, x):
    x = _clip((x - e0) / (e1 - e0))
    return x * x * (3 - 2 * x)


def _teal_orange(r, g, b):
    w = _smooth(0.15, 0.85, _luma(r, g, b))
    return r * (0.86 + 0.24 * w), g * (0.98 + 0.02 * w), b * (1.14 - 0.30 * w)


def _bleach(r, g, b):
    y = _luma(r, g, b)
    s = [x + (y - x) * 0.6 for x in (r, g, b)]
    return tuple(0.5 + (x - 0.5) * 1.3 for x in s)


def _warm_film(r, g, b):
    return tuple(0.035 + 0.94 * x for x in (r * 1.07, g * 1.01, b * 0.9))


def _cool_night(r, g, b):
    return r * 0.78, g * 0.9, b * 1.08


def _sepia(r, g, b):
    return (0.393 * r + 0.769 * g + 0.189 * b, 0.349 * r + 0.686 * g + 0.168 * b,
            0.272 * r + 0.534 * g + 0.131 * b)


def _cross_process(r, g, b):
    def s(x):
        return 0.5 - 0.5 * math.cos(math.pi * _clip(x))
    return s(r) * 1.05, g * 1.06 + 0.02, 0.12 + 0.76 * b


def _vintage(r, g, b):
    y = _luma(r, g, b)
    return tuple(0.08 + 0.82 * (x + (y - x) * 0.25) for x in (r * 1.04, g, b * 0.92))


def _mono_contrast(r, g, b):
    y = _luma(r, g, b)
    y = 0.5 + (y - 0.5) * 1.25
    return y, y, y


BUILTIN = {
    "Teal & orange": _teal_orange,
    "Bleach bypass": _bleach,
    "Warm film": _warm_film,
    "Cool night": _cool_night,
    "Sepia": _sepia,
    "Cross process": _cross_process,
    "Vintage fade": _vintage,
    "Contrasty black & white": _mono_contrast,
}


def builtin_lut_path(name: str, size: int = 33) -> str:
    """Path of the generated .cube for built-in look *name* (created once)."""
    from core.paths import cache_dir
    folder = os.path.join(cache_dir(), "luts")
    os.makedirs(folder, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    path = os.path.join(folder, f"{slug}_{size}.cube")
    if os.path.isfile(path):
        return path
    fn = BUILTIN[name]
    step = 1.0 / (size - 1)
    lines = [f'TITLE "{name}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    for bi in range(size):              # .cube order: red varies fastest
        for gi in range(size):
            for ri in range(size):
                r, g, b = (_clip(c) for c in fn(ri * step, gi * step, bi * step))
                lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


# ── Filter construction ──────────────────────────────────────────────────────


class LUTApplicatorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.lut_applicator"), t("lut.subtitle"), icon="🎨")
        body = scroll_body(self)

        pick = tk.LabelFrame(body, text=f"  {t('section.input_files')}  ", padx=px(12), pady=px(8))
        pick.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.src_row = FileRow(pick, t("lut.video_label"), self.src_var,
                               on_change=self._source_changed)
        self.src_row.pack(fill="x", pady=px(2))
        look_row = tk.Frame(pick)
        look_row.pack(fill="x", pady=px(2))
        tk.Label(look_row, text="Look:", width=16, anchor="w",
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.look_var = tk.StringVar(value=_CUSTOM)
        box = ttk.Combobox(look_row, textvariable=self.look_var, state="readonly", width=30,
                           values=[_CUSTOM, *BUILTIN])
        box.pack(side="left", padx=px(6))
        box.bind("<<ComboboxSelected>>", lambda _e: self._look_changed())
        self.lut_row = FileRow(pick, t("lut.lut_label"), kind="lut",
                               on_change=lambda _p: self._schedule())
        self.lut_row.pack(fill="x", pady=px(2))
        tk.Label(pick, text="  ℹ  3D/1D .cube, .3dl, .dat, .m3d, .csp and Hald CLUT images "
                            "(.png/.tif) are supported.",
                 fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(anchor="w")

        self.compare = CompareView(body, 400, 225, after_label="With LUT")
        self.compare.pack(pady=px(6))

        opts = tk.LabelFrame(body, text=f"  {t('lut.application_section')}  ", padx=px(12), pady=px(8))
        opts.pack(fill="x", padx=px(20), pady=px(4))
        r0 = tk.Frame(opts)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text=t("lut.intensity_label"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.intensity_var = tk.DoubleVar(value=1.0)
        tk.Scale(r0, variable=self.intensity_var, from_=0.0, to=1.0, resolution=0.01,
                 orient="horizontal", length=px(300),
                 command=lambda _v: self._schedule()).pack(side="left", padx=px(8))
        r1 = tk.Frame(opts)
        r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text=t("lut.interpolation_label")).pack(side="left")
        self.interp_var = tk.StringVar(value="tetrahedral")
        ib = ttk.Combobox(r1, textvariable=self.interp_var, values=INTERPOLATIONS,
                          state="readonly", width=14)
        ib.pack(side="left", padx=px(6))
        ib.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(r1, text=t("lut.interpolation_hint"), fg=CLR["fgdim"],
                 font=(UI_FONT, 9)).pack(side="left")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.src_row.path, "graded"))
        self.out_row.pack(fill="x", pady=px(8, 0))

        self.btn_render = tk.Button(body, text=t("lut.apply_button"), font=(UI_FONT, 12, "bold"),
                                    bg=CLR["pink"], fg="white", width=28, cursor="hand2",
                                    command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── Behaviour ────────────────────────────────────────────────────────────

    def lut_path(self) -> str:
        look = self.look_var.get()
        if look in BUILTIN:
            return builtin_lut_path(look)
        return self.lut_row.path

    def chain(self) -> str:
        path = self.lut_path()
        if not path or not os.path.isfile(path):
            return ""
        return build_chain(path, intensity=self.intensity_var.get(), interp=self.interp_var.get())

    def _look_changed(self):
        builtin = self.look_var.get() in BUILTIN
        self.lut_row.entry.config(state="disabled" if builtin else "normal")
        self._schedule()

    def _source_changed(self, path):
        self.compare.load(path)
        self.compare.set_filter(self.chain())
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "graded"))

    def _schedule(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(150, self._refresh)

    def _refresh(self):
        self._job = None
        self.compare.set_filter(self.chain())

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        lut = self.lut_path()
        if not lut or not os.path.isfile(lut):
            messagebox.showwarning(t("lut.no_lut_title"), t("lut.no_lut_message"), parent=self)
            return
        out = common.output_for(self, self.out_row, src, "graded", inputs=[lut])
        if not out:
            return
        self.log(self.console, f"Applying {os.path.basename(lut)} at "
                               f"{int(self.intensity_var.get() * 100)}% intensity…")
        common.render(self, src=src, info=info, out=out, chain=self.chain(), encoder=self.encoder,
                      console=self.console, btn=self.btn_render,
                      btn_label=t("lut.apply_button"), name="LUT")
