"""
tab_audiodynamics.py  ─  Audio Dynamics
Professional-grade audio processing chain using FFmpeg filters.
Equivalent to DaVinci Resolve Fairlight's dynamics and EQ tools.

Chain (applied in order):
  1. Noise Gate    - cuts audio below a threshold (kills room noise)
  2. EQ            - 5-band parametric equaliser
  3. Compressor    - controls dynamic range
  4. De-esser      - reduces harsh sibilance (S/SH sounds)
  5. Limiter       - hard ceiling to prevent clipping

Each stage can be bypassed independently.
Preview by exporting a short clip with the chain applied.
"""

import os
import tkinter as tk
from tkinter import ttk

from core.i18n import t
from core.ops import dynamics as dyn
from core.ops.shared import audio_filter_cmd
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.widgets import SaveRow, scroll_body

# ── EQ Band definitions ───────────────────────────────────────────────────
EQ_BANDS = [
    ("Low-shelf", "lowshelf", 80, t("dynamics.low_end_boost_for_warmth_cut_for_thinness")),
    ("Low-mid", "peaking", 300, t("dynamics.mud_zone_cut_to_clean_up_muddy_speech")),
    ("Presence", "peaking", 2500, t("dynamics.voice_presence_boost_for_clarity")),
    ("High-mid", "peaking", 5000, t("dynamics.air_brightness_boost_for_sparkle")),
    ("High-shelf", "highshelf", 10000, t("dynamics.top_end_air")),
]

VOICE_PRESETS = {
    t("dynamics.flat_bypass_all"): None,
    t("dynamics.podcast_voice_warm_clear"): {
        "gate_thresh": -40,
        "gate_ratio": 4,
        "comp_thresh": -18,
        "comp_ratio": 3,
        "comp_attack": 5,
        "comp_release": 80,
        "eq": [(80, -2), (300, -3), (2500, 3), (5000, 1), (10000, 0)],
        "limit_thresh": -1,
    },
    "YouTube voiceover: punchy": {
        "gate_thresh": -45,
        "gate_ratio": 6,
        "comp_thresh": -15,
        "comp_ratio": 4,
        "comp_attack": 3,
        "comp_release": 60,
        "eq": [(80, 2), (300, -4), (2500, 4), (5000, 2), (10000, 1)],
        "limit_thresh": -0.5,
    },
    "Music mastering: balanced": {
        "gate_thresh": -60,
        "gate_ratio": 2,
        "comp_thresh": -12,
        "comp_ratio": 2,
        "comp_attack": 20,
        "comp_release": 200,
        "eq": [(80, 0), (300, -1), (2500, 0), (5000, 1), (10000, 2)],
        "limit_thresh": -0.1,
    },
    "De-harsh: reduce harshness": {
        "gate_thresh": -50,
        "gate_ratio": 2,
        "comp_thresh": -20,
        "comp_ratio": 3,
        "comp_attack": 10,
        "comp_release": 100,
        "eq": [(80, 0), (300, -2), (2500, -3), (5000, -2), (10000, 0)],
        "limit_thresh": -1,
    },
}


class AudioDynamicsTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.controls: dict[str, tk.Variable] = {}  # every knob, by name (gate_thresh...)
        self.file_path = ""
        self._build_ui()

    def _build_ui(self):
        self._build_src()
        inner = self._build_scrollable_chain()
        self._build_1_noise_gate(inner)
        self._build_2_eq(inner)
        self._build_3_compressor(inner)
        self._build_4_de_esser(inner)
        self._build_5_limiter(inner)
        self._build_filter_preview(inner)
        self._build_output_render(inner)

    def _build_src(self):
        make_header(self, t("tab.audio_dynamics"), t("dynamics.desc_header"), icon="🎛")

        # Source
        sf = tk.Frame(self)
        sf.pack(pady=px(8))
        tk.Label(sf, text=t("common.source_file"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self.src_var = tk.StringVar()
        tk.Entry(sf, textvariable=self.src_var, width=58, relief="flat").pack(
            side="left", padx=px(8)
        )
        tk.Button(
            sf, text=t("btn.browse"), command=self._browse, cursor="hand2", relief="flat"
        ).pack(side="left")

        # Preset
        pre_f = tk.Frame(self)
        pre_f.pack(fill="x", padx=px(16), pady=px(4))
        tk.Label(pre_f, text=t("dynamics.lbl_quick_preset"), font=(UI_FONT, 10, "bold")).pack(
            side="left"
        )
        self.preset_var = tk.StringVar(value=next(iter(VOICE_PRESETS.keys())))
        preset_cb = ttk.Combobox(
            pre_f,
            textvariable=self.preset_var,
            values=list(VOICE_PRESETS.keys()),
            state="readonly",
            width=34,
        )
        preset_cb.pack(side="left", padx=px(8))
        preset_cb.bind("<<ComboboxSelected>>", self._apply_preset)

    def _build_scrollable_chain(self):
        """Scrollable chain."""
        # ── Scrollable chain ─────────────────────────────────────────────
        return scroll_body(self)

    def _build_1_noise_gate(self, inner):
        """1. Noise Gate."""
        # ── 1. Noise Gate ─────────────────────────────────────────────────
        self._build_gate(inner)

    def _build_2_eq(self, inner):
        """2. EQ."""
        # ── 2. EQ ─────────────────────────────────────────────────────────
        self._build_eq(inner)

    def _build_3_compressor(self, inner):
        """3. Compressor."""
        # ── 3. Compressor ─────────────────────────────────────────────────
        self._build_compressor(inner)

    def _build_4_de_esser(self, inner):
        """4. De-esser."""
        # ── 4. De-esser ───────────────────────────────────────────────────
        self._build_deesser(inner)

    def _build_5_limiter(self, inner):
        """5. Limiter."""
        # ── 5. Limiter ────────────────────────────────────────────────────
        self._build_limiter(inner)

    def _build_filter_preview(self, inner):
        """Filter preview."""
        # ── Filter preview ────────────────────────────────────────────────
        self.filter_preview = tk.Label(
            inner, text="", fg=CLR["fgdim"], font=(MONO_FONT, 8), wraplength=px(860), justify="left"
        )
        self.filter_preview.pack(anchor="w", padx=px(16), pady=px(4))

    def _build_output_render(self, inner):
        """Output & render."""
        # ── Output & render ───────────────────────────────────────────────
        self.out_var = tk.StringVar()
        SaveRow(inner, t("common.output_file"), self.out_var, kind="media").pack(
            pady=px(6), padx=px(16), fill="x"
        )

        btn_row = tk.Frame(inner)
        btn_row.pack(pady=px(8), padx=px(16))
        tk.Button(
            btn_row,
            text=t("dynamics.btn_preview_10s"),
            bg=CLR["accent"],
            fg="white",
            width=16,
            font=(UI_FONT, 10),
            command=self._preview,
        ).pack(side="left", padx=px(8))
        self.btn_render = self.make_render_btn(
            btn_row, t("dynamics.btn_apply_dynamics"), self._render, width=24
        )
        self.btn_render.pack(side="left", padx=px(8))

        self.console = self.make_console_block(
            inner, height=5, padx=px(16), pady=px(4), expand=False
        )

    # ── Stage builders ────────────────────────────────────────────────────
    def _stage_frame(self, parent, title, bypass_attr):
        lf = tk.LabelFrame(parent, text=f"  {title}  ", padx=px(14), pady=px(8))
        lf.pack(fill="x", padx=px(16), pady=px(4))
        var = tk.BooleanVar(value=True)
        self.controls[bypass_attr] = var
        tk.Checkbutton(
            lf, text=t("common.enable"), variable=var, command=self._update_filter_preview
        ).pack(anchor="e")
        return lf

    def _slider_row(self, parent, label, attr, lo, hi, default, res, unit=""):
        row = tk.Frame(parent)
        row.pack(fill="x", pady=px(2))
        tk.Label(row, text=label, width=22, anchor="e").pack(side="left")
        var = tk.DoubleVar(value=default)
        self.controls[attr] = var
        sl = tk.Scale(
            row,
            variable=var,
            from_=lo,
            to=hi,
            resolution=res,
            orient="horizontal",
            length=px(240),
            command=lambda v: self._update_filter_preview(),
        )
        sl.pack(side="left", padx=px(6))
        lbl = tk.Label(row, text=f"{default}{unit}", width=8, fg=CLR["accent"])
        lbl.pack(side="left")
        var.trace_add(
            "write", lambda *_, label=lbl, v=var, u=unit: label.config(text=f"{v.get():.2f}{u}")
        )

    def _build_gate(self, parent):
        lf = self._stage_frame(parent, "🚪  1. Noise Gate", "gate_on")
        self._slider_row(lf, "Threshold (dB):", "gate_thresh", -80, 0, -40, 0.5, " dB")
        self._slider_row(lf, "Ratio:", "gate_ratio", 1, 10, 4, 0.5, ":1")
        self._slider_row(lf, "Attack (ms):", "gate_attack", 1, 200, 5, 1, " ms")
        self._slider_row(lf, "Release (ms):", "gate_release", 10, 2000, 80, 10, " ms")
        tk.Label(
            lf,
            text=t("dynamics.cuts_everything_below_the_threshold_silences_roo"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

    def _build_eq(self, parent):
        lf = self._stage_frame(parent, "🎚  2. Parametric EQ  (5 bands)", "eq_on")
        self._eq_vars = {}
        for i, (name, ftype, freq, tip) in enumerate(EQ_BANDS):
            row = tk.Frame(lf)
            row.pack(fill="x", pady=px(2))
            tk.Label(row, text=f"{name} ({freq} Hz):", width=22, anchor="e").pack(side="left")
            var = tk.DoubleVar(value=0.0)
            self._eq_vars[i] = (var, ftype, freq)
            sl = tk.Scale(
                row,
                variable=var,
                from_=-12,
                to=12,
                resolution=0.5,
                orient="horizontal",
                length=px(240),
                command=lambda v: self._update_filter_preview(),
            )
            sl.pack(side="left", padx=px(6))
            lbl = tk.Label(row, text=t("dynamics.0_0_db"), width=8, fg=CLR["accent"])
            lbl.pack(side="left")
            var.trace_add(
                "write", lambda *_, label=lbl, v=var: label.config(text=f"{v.get():.1f} dB")
            )
            tk.Label(row, text=tip, fg=CLR["fgdim"], font=(UI_FONT, 7)).pack(
                side="left", padx=px(6)
            )

    def _build_compressor(self, parent):
        lf = self._stage_frame(parent, "🗜  3. Compressor", "comp_on")
        self._slider_row(lf, "Threshold (dB):", "comp_thresh", -60, 0, -18, 0.5, " dB")
        self._slider_row(lf, "Ratio:", "comp_ratio", 1, 20, 3, 0.5, ":1")
        self._slider_row(lf, "Attack (ms):", "comp_attack", 0.1, 200, 5, 0.1, " ms")
        self._slider_row(lf, "Release (ms):", "comp_release", 10, 2000, 80, 10, " ms")
        self._slider_row(lf, "Makeup gain (dB):", "comp_makeup", 0, 24, 0, 0.5, " dB")
        tk.Label(
            lf,
            text=t("dynamics.raises_quiet_parts_and_lowers_loud_peaks_makes_s"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

    def _build_deesser(self, parent):
        lf = self._stage_frame(parent, "🐍  4. De-esser", "deess_on")
        self._slider_row(lf, "Frequency (Hz):", "deess_freq", 2000, 16000, 6000, 100, " Hz")
        self._slider_row(lf, "Threshold:", "deess_thresh", 0, 1, 0.1, 0.01, "")
        tk.Label(
            lf,
            text=t("dynamics.targets_harsh_s_sh_sibilance_at_the_chosen_frequ"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

    def _build_limiter(self, parent):
        lf = self._stage_frame(parent, "🔔  5. Limiter", "limit_on")
        self._slider_row(lf, "Ceiling (dBFS):", "limit_thresh", -12, 0, -1, 0.1, " dBFS")
        tk.Label(
            lf,
            text=t("dynamics.hard_ceiling_prevents_any_peak_from_exceeding_th"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

    # ── Preset ────────────────────────────────────────────────────────────
    def _apply_preset(self, *_):
        p = VOICE_PRESETS.get(self.preset_var.get())
        if not p:
            return
        self.controls["gate_thresh"].set(p["gate_thresh"])
        self.controls["gate_ratio"].set(p["gate_ratio"])
        self.controls["comp_thresh"].set(p["comp_thresh"])
        self.controls["comp_ratio"].set(p["comp_ratio"])
        self.controls["comp_attack"].set(p["comp_attack"])
        self.controls["comp_release"].set(p["comp_release"])
        self.controls["limit_thresh"].set(p["limit_thresh"])
        for i, gain in enumerate(p["eq"]):
            if i < len(EQ_BANDS):
                self._eq_vars[i][0].set(gain[1])
        self._update_filter_preview()

    # ── Filter string ─────────────────────────────────────────────────────
    def _build_filter(self) -> str:
        c = self.controls
        eq = [
            (ftype, freq, max(-24.0, min(24.0, float(var.get()))))
            for var, ftype, freq in self._eq_vars.values()
        ]
        spec = dyn.Dynamics(
            gate=dyn.Gate(
                c["gate_thresh"].get(),
                c["gate_ratio"].get(),
                c["gate_attack"].get(),
                c["gate_release"].get(),
            )
            if c["gate_on"].get()
            else None,
            eq=eq if c["eq_on"].get() else [],
            compressor=dyn.Compressor(
                c["comp_thresh"].get(),
                c["comp_ratio"].get(),
                c["comp_attack"].get(),
                c["comp_release"].get(),
                c["comp_makeup"].get(),
            )
            if c["comp_on"].get()
            else None,
            deesser=(float(c["deess_freq"].get()), float(c["deess_thresh"].get()))
            if c["deess_on"].get()
            else None,
            limit=c["limit_thresh"].get() if c["limit_on"].get() else None,
            sample_rate=getattr(self, "_sample_rate", 0) or 48000,
        )
        return dyn.dynamics_filter(spec)

    def _update_filter_preview(self):
        filt = self._build_filter()
        short = filt[:180] + "…" if len(filt) > 180 else filt
        self.filter_preview.config(text=f"Filter chain:  {short}")

    # ─────────────────────────────────────────────────────────────────────
    def _browse(self):
        p = dialogs.askopenfilename(
            filetypes=[
                ("Media", "*.mp4 *.mov *.mkv *.avi *.mp3 *.wav *.aac *.flac"),
                ("All", t("ducker.item_2")),
            ]
        )
        if p:
            self.file_path = p
            self.src_var.set(p)
            base = os.path.splitext(p)[0]
            self.out_var.set(base + "_dynamics.mp4")
        self._update_filter_preview()

    def _preview(self):
        """Listen to 12 seconds with the chain applied (in-app, no files left behind)."""
        from tabs import common

        src = self.src_var.get().strip()
        self._probe_rate(src)
        common.preview_audio(self, src, self._build_filter())

    def _probe_rate(self, src):
        from core import ffmpeg as ff

        info = ff.try_probe(src) if src else None
        self._sample_rate = (
            info.require_audio().sample_rate if info is not None and info.audio else 48000
        )
        return info

    def _render(self):
        from tabs import common

        src = self.src_var.get().strip()
        info = common.check_source(self, src, need_video=False)
        if info is None:
            return
        self._probe_rate(src)
        out = self.out_var.get().strip() or self.suggest_output(src, "dynamics")
        out = self.confirm_output(out, [src])
        if not out:
            return
        self.out_var.set(out)
        cmd = audio_filter_cmd(src, self._build_filter(), out, info)
        self.log(self.console, t("log.dynamics.applying_audio_dynamics_chain"))
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=info.duration or None,
            output=out,
            inputs=[src],
            name="Audio Dynamics",
        )
