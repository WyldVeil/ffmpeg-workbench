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

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import CompareView, EncoderPanel, FileRow, scroll_body

_SQ = [i * i for i in range(256)]
_SAMPLE_W, _SAMPLE_H = 160, 90


def sample_times(info: ff.MediaInfo, count: int = 10) -> list:
    if info.is_image or info.duration <= 0.2:
        return [0.0]
    d = info.duration
    count = max(1, min(count, int(d * 4) or 1))
    if count == 1:
        return [d / 2]
    return [d * (0.08 + 0.84 * i / (count - 1)) for i in range(count)]


def rgb_stats(path: str, count: int = 10) -> dict:
    """{'R': (mean, std), 'G': ..., 'B': ...} over *count* sampled frames.

    Raises RuntimeError when no frame could be decoded."""
    info = ff.probe(path)
    raw = bytearray()
    for at in sample_times(info, count):
        cmd = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error"]
        if at > 0:
            cmd += ["-ss", ff.ts(at)]
        cmd += [
            "-i",
            path,
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-vf",
            f"scale={_SAMPLE_W}:{_SAMPLE_H}:flags=area,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
        r = ff.capture(cmd, timeout=60, text=False)
        frame = r.stdout or b""
        if r.returncode == 0 and len(frame) == _SAMPLE_W * _SAMPLE_H * 3:
            raw += frame
    if not raw:
        raise RuntimeError(f"Could not decode any frames from {path}")
    data = bytes(raw)
    out = {}
    for i, ch in enumerate("RGB"):
        chan = data[i::3]
        n = len(chan)
        mean = sum(chan) / n
        var = max(sum(map(_SQ.__getitem__, chan)) / n - mean * mean, 0.0)
        out[ch] = (mean, var**0.5)
    return out


def build_filter(
    src: dict, ref: dict, *, strength: float = 1.0, channels: str = "RGB", contrast: bool = True
) -> str:
    """lutrgb chain moving *src* statistics towards *ref* ('' if nothing to do)."""
    parts = []
    for ch in "RGB":
        if ch not in channels:
            continue
        sm, ss = src[ch]
        rm, rs = ref[ch]
        k = min(max(rs / max(ss, 1.0), 0.25), 4.0) if contrast else 1.0
        expr = f"clip(val+{strength:.3f}*((val-{sm:.2f})*{k:.4f}+{rm:.2f}-val),0,255)"
        parts.append(f"{ch.lower()}='{expr}'")
    return "lutrgb=" + ":".join(parts) if parts else ""


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

        stats_lf = tk.LabelFrame(
            body, text=f"  {t('color_match.statistics_section')}  ", padx=px(12), pady=px(6)
        )
        stats_lf.pack(fill="x", padx=px(20), pady=px(4))
        self.stats_text = tk.Text(
            stats_lf,
            height=5,
            bg=CLR["console_bg"],
            fg="#00FF88",
            font=(MONO_FONT, 9),
            relief="flat",
        )
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

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(
            out_lf, codecs=("h264", "hevc", "av1", "vp9", "prores", "dnxhr")
        )
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(
            out_lf,
            t("common.output_file"),
            save=True,
            suggest=lambda: self.suggest_output(self.src_row.path, "matched"),
        )
        self.out_row.pack(fill="x", pady=px(8, 0))

        self.btn_render = tk.Button(
            body,
            text=t("color_match.apply_button"),
            font=(UI_FONT, 12, "bold"),
            bg="#AD1457",
            fg="white",
            width=26,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

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
        return build_filter(
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
            return rgb_stats(src), rgb_stats(ref)

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
            src=src,
            info=info,
            out=out,
            chain=chain,
            encoder=self.encoder,
            console=self.console,
            btn=self.btn_render,
            name="Colour Match",
        )
