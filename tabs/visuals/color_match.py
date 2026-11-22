"""
tabs/visuals/color_match.py  -  Colour Match

Match the colour and contrast of a clip to a reference clip or still.

Both files are sampled at several frames (downscaled RGB decoded by FFmpeg)
and the per-channel spatial mean and standard deviation are measured.  Each
channel is then remapped with ``lutrgb``:

    out = (in - src_mean) * (ref_std / src_std) + ref_mean

blended with the original by the chosen strength (Reinhard-style statistical
transfer, applied per RGB channel).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from core import tasks
from core.i18n import t
from core.ops import color_match as cm
from core.ops.shared import RenderJob
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import CompareView, FileRow, output_section, scroll_body


class ColorMatchTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._stats = None  # (src_path, ref_path, src_stats, ref_stats)
        self._analysing = False
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.make_header(self, t("tab.colour_match"), t("color_match.subtitle"), icon="🎨")
        body = scroll_body(self)
        self._build_inputs(body)
        self._build_match_options(body)
        self._build_stats(body)
        self._build_output(body)

    def _build_inputs(self, body):
        """The clip to fix and the reference to match, and the before/after view."""
        inp = tk.LabelFrame(body, text=f"  {t('section.input_clips')}  ", padx=px(12), pady=px(8))
        inp.pack(fill="x", padx=px(20), pady=px(10, 4))
        self.src_row = FileRow(inp, "Clip to fix:", self.src_var, on_change=self._inputs_changed)
        self.src_row.pack(fill="x", pady=px(2))
        tk.Label(
            inp, text=f"  ℹ  {t('color_match.source_hint')}", fg=CLR["fgdim"], font=(UI_FONT, 8)
        ).pack(anchor="w")
        self.ref_row = FileRow(
            inp, "Reference look:", kind="visual", on_change=self._inputs_changed
        )
        self.ref_row.pack(fill="x", pady=px(2))
        tk.Label(
            inp, text=f"  ℹ  {t('color_match.reference_hint')}", fg=CLR["fgdim"], font=(UI_FONT, 8)
        ).pack(anchor="w")

        self.compare = CompareView(body, 400, 225, after_label="Matched")
        self.compare.pack(pady=px(6))

    def _build_match_options(self, body):
        """Strength, channels and contrast."""
        opts = tk.LabelFrame(
            body, text=f"  {t('color_match.match_options_section')}  ", padx=px(12), pady=px(8)
        )
        opts.pack(fill="x", padx=px(20), pady=px(4))
        r0 = tk.Frame(opts)
        r0.pack(fill="x", pady=px(2))
        tk.Label(r0, text=t("color_match.strength_label"), font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self.strength_var = tk.DoubleVar(value=1.0)
        tk.Scale(
            r0,
            variable=self.strength_var,
            from_=0.0,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            length=px(260),
            command=lambda _v: self._update_filter(),
        ).pack(side="left", padx=px(8))
        r1 = tk.Frame(opts)
        r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text=t("color_match.channels_label"), font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self.ch_vars = {}
        for ch in "RGB":
            var = tk.BooleanVar(value=True)
            self.ch_vars[ch] = var
            tk.Checkbutton(
                r1, text=ch, variable=var, font=(UI_FONT, 10, "bold"), command=self._update_filter
            ).pack(side="left", padx=px(6))
        self.contrast_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            r1, text="Match contrast too", variable=self.contrast_var, command=self._update_filter
        ).pack(side="left", padx=px(16, 0))

    def _build_stats(self, body):
        """What the analysis found, and the Analyse button."""
        stats_lf = tk.LabelFrame(
            body, text=f"  {t('color_match.statistics_section')}  ", padx=px(12), pady=px(6)
        )
        stats_lf.pack(fill="x", padx=px(20), pady=px(4))
        self.stats_text, _bar = self.make_console(stats_lf, height=5)
        self.stats_text.pack(fill="x")
        self._show_stats("Choose both files, then press Analyse.")
        self.btn_analyse = tk.Button(
            stats_lf,
            text=t("color_match.analyse_button"),
            font=(UI_FONT, 10, "bold"),
            bg="#37474F",
            fg="white",
            cursor="hand2",
            command=self._analyse,
        )
        self.btn_analyse.pack(anchor="w", pady=px(6, 0))

    def _build_output(self, body):
        """Encoder, where the result goes, the button and the log."""
        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1", "vp9", "prores", "dnxhr"),
            suggest=lambda: self.suggest_output(self.src_row.path, "matched"),
        )

        self.btn_render = self.make_render_btn(
            body, t("color_match.apply_button"), self._render, width=26
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    # ── Behaviour ────────────────────────────────────────────────────────────

    def _inputs_changed(self, _path=None):
        src = self.src_row.path
        if src and src != self.compare.view_before.path:
            self.compare.load(src)
            if not self.out_row.path:
                self.out_row.set(self.suggest_output(src, "matched"))
        self._update_filter()

    def _current_stats(self):
        if (
            self._stats
            and self._stats[0] == self.src_row.path
            and self._stats[1] == self.ref_row.path
        ):
            return self._stats[2], self._stats[3]
        return None

    def current_filter(self) -> str:
        stats = self._current_stats()
        if stats is None:
            return ""
        channels = "".join(ch for ch, v in self.ch_vars.items() if v.get())
        return cm.build_filter(
            *stats,
            strength=self.strength_var.get(),
            channels=channels,
            contrast=self.contrast_var.get(),
        )

    def _update_filter(self):
        self.compare.set_filter(self.current_filter())

    def _show_stats(self, text):
        self.stats_text.config(state="normal")
        self.stats_text.delete("1.0", tk.END)
        self.stats_text.insert(tk.END, text)
        self.stats_text.config(state="disabled")

    def _analyse(self, then=None):
        src, ref = self.src_row.path, self.ref_row.path
        if common.check_source(self, src, what="Clip to fix") is None:
            return
        if common.check_source(self, ref, what="Reference") is None:
            return
        if self._analysing:
            return
        self._analysing = True
        self.btn_analyse.config(state="disabled", text=t("loudness.analysing_2"))
        self.log(self.console, t("log.color_match.extracting_colour_statistics"))

        def work():
            return cm.rgb_stats(src), cm.rgb_stats(ref)

        def done(result, error=None):
            self._analysing = False
            self.btn_analyse.config(state="normal", text=t("color_match.analyse_button"))
            if error is not None or result is None:
                self.log_tagged(self.console, f"Analysis failed: {error}", "error")
                messagebox.showerror(t("common.error"), f"Analysis failed:\n{error}", parent=self)
                return
            s, r = result
            self._stats = (src, ref, s, r)
            lines = ["Channel   clip mean / spread     reference mean / spread", "─" * 56]
            for ch in "RGB":
                lines.append(
                    f"  {ch}       {s[ch][0]:6.1f} / {s[ch][1]:5.1f}"
                    f"          {r[ch][0]:6.1f} / {r[ch][1]:5.1f}"
                )
            self._show_stats("\n".join(lines))
            self.log(self.console, t("log.color_match.analysis_complete"))
            self._update_filter()
            if then is not None:
                then()

        tasks.submit(
            work, on_done=done, on_error=lambda exc: done(None, exc), name="ColourMatchAnalyse"
        )

    def _render(self):
        src = self.src_row.path
        info = common.check_source(self, src, what="Clip to fix")
        if info is None:
            return
        if self._current_stats() is None:
            self._analyse(then=self._render)
            return
        chain = self.current_filter()
        if not chain:
            messagebox.showwarning(
                t("color_match.no_filter_title"), t("color_match.no_filter_message"), parent=self
            )
            return
        out = common.output_for(self, self.out_row, src, "matched", inputs=[self.ref_row.path])
        if not out:
            return
        common.render(
            self,
            RenderJob(src=src, info=info, out=out, chain=chain, name="Colour Match"),
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
        )
