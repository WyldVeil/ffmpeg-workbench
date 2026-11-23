"""
tabs/visuals/lut.py  -  LUT Applicator

Apply a colour look-up table: 3D .cube/.3dl/.dat/.m3d/.csp files, 1D .cube
files, Hald CLUT images (.png/.tif), or one of the built-in looks.  An
intensity slider blends the look with the original, and the before/after
preview shows exactly what will be rendered.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core.i18n import t
from core.ops.lut import BUILTIN, INTERPOLATIONS, build_chain, builtin_lut_path
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, FileRow, output_section, scroll_body

_CUSTOM = "Use a LUT file…"


# ── Filter construction ──────────────────────────────────────────────────────


class LUTApplicatorTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._job: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(self, t("tab.lut_applicator"), t("lut.subtitle"), icon="🎨")
        body = scroll_body(self)
        self._build_look_picker(body)
        self._build_lut_options(body)
        self._build_output(body)

    def _build_look_picker(self, body: tk.Frame) -> None:
        """The video and the look: a built-in one or a LUT file."""
        pick = tk.LabelFrame(body, text=f"  {t('section.input_files')}  ", padx=px(12), pady=px(8))
        pick.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.src_row = FileRow(
            pick, t("lut.video_label"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", pady=px(2))
        look_row = tk.Frame(pick)
        look_row.pack(fill="x", pady=px(2))
        tk.Label(look_row, text="Look:", width=16, anchor="w", font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self.look_var = tk.StringVar(value=_CUSTOM)
        box = ttk.Combobox(
            look_row,
            textvariable=self.look_var,
            state="readonly",
            width=30,
            values=[_CUSTOM, *BUILTIN],
        )
        box.pack(side="left", padx=px(6))
        box.bind("<<ComboboxSelected>>", lambda _e: self._look_changed())
        self.lut_row = FileRow(
            pick, t("lut.lut_label"), kind="lut", on_change=lambda _p: self._schedule()
        )
        self.lut_row.pack(fill="x", pady=px(2))
        tk.Label(
            pick,
            text="  ℹ  3D/1D .cube, .3dl, .dat, .m3d, .csp and Hald CLUT images "
            "(.png/.tif) are supported.",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

        self.compare = CompareView(body, 400, 225, after_label="With LUT")
        self.compare.pack(pady=px(6))

    def _build_lut_options(self, body: tk.Frame) -> None:
        """Intensity and interpolation."""
        opts = tk.LabelFrame(
            body, text=f"  {t('lut.application_section')}  ", padx=px(12), pady=px(8)
        )
        opts.pack(fill="x", padx=px(20), pady=px(4))
        r0 = tk.Frame(opts)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text=t("lut.intensity_label"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.intensity_var = tk.DoubleVar(value=1.0)
        tk.Scale(
            r0,
            variable=self.intensity_var,
            from_=0.0,
            to=1.0,
            resolution=0.01,
            orient="horizontal",
            length=px(300),
            command=lambda _v: self._schedule(),
        ).pack(side="left", padx=px(8))
        r1 = tk.Frame(opts)
        r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text=t("lut.interpolation_label")).pack(side="left")
        self.interp_var = tk.StringVar(value="tetrahedral")
        ib = ttk.Combobox(
            r1, textvariable=self.interp_var, values=INTERPOLATIONS, state="readonly", width=14
        )
        ib.pack(side="left", padx=px(6))
        ib.bind("<<ComboboxSelected>>", lambda _e: self._schedule())
        tk.Label(r1, text=t("lut.interpolation_hint"), fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(
            side="left"
        )

    def _build_output(self, body: tk.Frame) -> None:
        """Encoder, where the result goes, the button and the log."""
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores", "dnxhr"),
            suggest=lambda: self.suggest_output(self.src_row.path, "graded"),
        )

        self.btn_render = self.make_render_btn(body, t("lut.apply_button"), self._render, width=28)
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=6)

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

    def _look_changed(self) -> None:
        builtin = self.look_var.get() in BUILTIN
        self.lut_row.entry.config(state="disabled" if builtin else "normal")
        self._schedule()

    def _source_changed(self, path: str) -> None:
        self.compare.load(path)
        self.compare.set_filter(self.chain())
        if not self.out_row.path:
            self.out_row.set(self.suggest_output(path, "graded"))

    def _schedule(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(150, self._refresh)

    def _refresh(self) -> None:
        self._job = None
        self.compare.set_filter(self.chain())

    def _render(self) -> None:
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
        self.log(
            self.console,
            f"Applying {os.path.basename(lut)} at "
            f"{int(self.intensity_var.get() * 100)}% intensity…",
        )
        common.render(
            self,
            RenderJob(src=src, info=info, out=out, chain=self.chain(), name="LUT"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
