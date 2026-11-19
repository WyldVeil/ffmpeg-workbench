"""
tabs/transcoder/compare.py  -  Compare & Quality

How much quality did an encode lose?  Measures VMAF (what viewers notice),
SSIM and PSNR between an original and a copy (encoded, compressed, scaled,
uploaded and downloaded again...), shows the score over time, the worst
moments, and both videos side by side at any moment.  See core/quality.py.
"""
from __future__ import annotations

import json
import math
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import dialogs
from core import ffmpeg as ff
from core import quality as q
from core.i18n import t
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import FileRow, FramePreview, scroll_body

SPEEDS = {1: "Every frame (most exact)", 2: "Every 2nd frame", 5: "Every 5th frame (fastest)"}


def _fmt(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:04.1f}"


def _size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


class ABView(tk.Frame):
    """The original and the copy side by side at the same moment."""

    def __init__(self, parent, width=400, height=225):
        super().__init__(parent, bg=CLR["bg"])
        row = tk.Frame(self, bg=CLR["bg"])
        row.pack()
        self.views = []
        for label in ("Original", "Encoded"):
            col = tk.Frame(row, bg=CLR["bg"])
            col.pack(side="left", padx=4)
            tk.Label(col, text=label, bg=CLR["bg"], fg=CLR["fgdim"], font=(UI_FONT, 9, "bold")).pack(anchor="w")
            view = FramePreview(col, width, height, show_time=False)
            view.pack()
            self.views.append(view)
        bar = tk.Frame(self, bg=CLR["bg"])
        bar.pack(fill="x", pady=(4, 0))
        self.pos = tk.DoubleVar(value=0.0)
        self.scale = ttk.Scale(bar, from_=0.0, to=1.0, variable=self.pos, command=lambda _v: self.show(self.pos.get()))
        self.scale.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self.time_lbl = tk.Label(bar, text="", width=10, bg=CLR["bg"], fg=CLR["fgdim"], font=(MONO_FONT, 9))
        self.time_lbl.pack(side="right")
        self.skip_ref = 0.0
        self.skip_dist = 0.0
        self.duration = 0.0

    def load(self, ref, dist, ref_dur, dist_dur):
        self.duration = ref_dur or dist_dur or 0.0
        self.scale.configure(to=max(0.01, self.duration))
        at = self.duration * 0.25
        self.pos.set(at)
        self.views[0].load(ref, ref_dur, at)
        self.views[1].load(dist, dist_dur, self._dist_time(at))
        self.time_lbl.config(text=_fmt(at))

    def _dist_time(self, at: float) -> float:
        return max(0.0, at - self.skip_ref + self.skip_dist)

    def show(self, at: float):
        at = max(0.0, min(float(at), self.duration))
        self.pos.set(at)
        self.time_lbl.config(text=_fmt(at))
        if self.views[0].path:
            self.views[0].show(at)
        if self.views[1].path:
            self.views[1].show(self._dist_time(at))


class CompareQualityTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self._infos = (None, None)
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, "Compare & Quality", "How much quality did an encode lose? VMAF, SSIM and PSNR, "
                                                   "the worst moments, and both videos side by side.", icon="📊")
        body = scroll_body(self)
        self.ref_row = FileRow(body, "Original:", on_change=lambda _p: self._files_changed())
        self.ref_row.pack(fill="x", padx=20, pady=(10, 2))
        self.dist_row = FileRow(body, "Encoded copy:", on_change=lambda _p: self._files_changed())
        self.dist_row.pack(fill="x", padx=20, pady=2)
        self.info_lbl = tk.Label(body, text="Choose the original and the encoded (or edited) copy to compare.",
                                 fg=CLR["fgdim"], anchor="w", justify="left")
        self.info_lbl.pack(fill="x", padx=24, pady=(2, 4))
        self.ab = ABView(body)
        self.ab.pack(pady=4)

        opt = tk.LabelFrame(body, text="  Measure  ", padx=12, pady=8)
        opt.pack(fill="x", padx=20, pady=4)
        r1 = tk.Frame(opt)
        r1.pack(fill="x")
        self.metric_vars = {m: tk.BooleanVar(value=True) for m in q.METRICS}
        for m, text in (("vmaf", "VMAF - what viewers notice"), ("ssim", "SSIM - structure"), ("psnr", "PSNR - pixel "
                                                                                                         "error")):
            tk.Checkbutton(r1, text=text, variable=self.metric_vars[m]).pack(side="left", padx=(0, 12))
        r2 = tk.Frame(opt)
        r2.pack(fill="x", pady=(6, 0))
        tk.Label(r2, text="Viewing:").pack(side="left")
        self.model_var = tk.StringVar(value=q.MODELS["hd"][0])
        ttk.Combobox(r2, textvariable=self.model_var, values=[v[0] for v in q.MODELS.values()], state="readonly",
                     width=32).pack(side="left", padx=(4, 16))
        tk.Label(r2, text="Speed:").pack(side="left")
        self.speed_var = tk.StringVar(value=SPEEDS[1])
        ttk.Combobox(r2, textvariable=self.speed_var, values=list(SPEEDS.values()), state="readonly",
                     width=24).pack(side="left", padx=4)
        r3 = tk.Frame(opt)
        r3.pack(fill="x", pady=(6, 0))
        tk.Label(r3, text="Skip at the start - original:").pack(side="left")
        self.skip_ref_var = tk.StringVar(value="0")
        tk.Entry(r3, textvariable=self.skip_ref_var, width=6).pack(side="left", padx=4)
        tk.Label(r3, text="s   encoded:").pack(side="left")
        self.skip_dist_var = tk.StringVar(value="0")
        tk.Entry(r3, textvariable=self.skip_dist_var, width=6).pack(side="left", padx=4)
        tk.Label(r3, text="s   (if one was trimmed)").pack(side="left")
        for v in (self.skip_ref_var, self.skip_dist_var):
            v.trace_add("write", lambda *_a: self._skips_changed())
        r4 = tk.Frame(opt)
        r4.pack(fill="x", pady=(6, 0))
        self.limit_on = tk.BooleanVar(value=False)
        tk.Checkbutton(r4, text="Only the first", variable=self.limit_on).pack(side="left")
        self.limit_var = tk.StringVar(value="30")
        tk.Entry(r4, textvariable=self.limit_var, width=6).pack(side="left", padx=4)
        tk.Label(r4, text="seconds").pack(side="left")

        self.btn = tk.Button(body, text="📊  MEASURE QUALITY", font=(UI_FONT, 12, "bold"), bg=CLR["green"],
                             fg="white", width=24, cursor="hand2", command=self._run)
        self.btn.pack(pady=8)

        self.res = tk.LabelFrame(body, text="  Results  ", padx=12, pady=8)
        self.res.pack(fill="x", padx=20, pady=4)
        scores = tk.Frame(self.res)
        scores.pack(fill="x")
        self.score_lbls = {}
        for m, title in (("vmaf", "VMAF"), ("ssim", "SSIM"), ("psnr", "PSNR")):
            card = tk.Frame(scores, bg=CLR["panel"], padx=14, pady=8)
            card.pack(side="left", padx=(0, 10), fill="y")
            tk.Label(card, text=title, bg=CLR["panel"], fg=CLR["fgdim"], font=(UI_FONT, 9, "bold")).pack(anchor="w")
            big = tk.Label(card, text="–", bg=CLR["panel"], fg=CLR["fg"], font=(UI_FONT, 20, "bold"))
            big.pack(anchor="w")
            small = tk.Label(card, text="", bg=CLR["panel"], fg=CLR["fgdim"], font=(UI_FONT, 9), justify="left")
            small.pack(anchor="w")
            self.score_lbls[m] = (big, small)
        self.verdict_lbl = tk.Label(self.res, text="", font=(UI_FONT, 11, "bold"), anchor="w")
        self.verdict_lbl.pack(fill="x", pady=(8, 2))
        self.graph = tk.Canvas(self.res, height=150, bg=CLR["console_bg"], highlightthickness=0, cursor="hand2")
        self.graph.pack(fill="x", pady=4)
        self.graph.bind("<Configure>", lambda _e: self._draw_graph())
        self.graph.bind("<Button-1>", self._graph_click)
        self.worst_frame = tk.Frame(self.res)
        self.worst_frame.pack(fill="x", pady=(2, 4))
        tk.Button(self.res, text="💾  Save the scores (CSV + JSON)…", relief="flat", command=self._export).pack(
            anchor="w")
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=20, pady=(4, 10))
        self.console, csb = self.make_console(cf, height=4)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── Files ─────────────────────────────────────────────────────────
    def load_file(self, path) -> bool:
        """Dropped files: the first is the original, the next the copy."""
        if not self.ref_row.path or (self.ref_row.path and self.dist_row.path):
            self.ref_row.set(path)
            self.dist_row.set("")
        else:
            self.dist_row.set(path)
        self._files_changed()
        return True

    def _files_changed(self):
        ref, dist = self.ref_row.path, self.dist_row.path
        ri = ff.try_probe(ref) if ref and os.path.isfile(ref) else None
        di = ff.try_probe(dist) if dist and os.path.isfile(dist) else None
        self._infos = (ri, di)
        parts = []
        for label, info, path in (("Original", ri, ref), ("Copy", di, dist)):
            if info is not None and info.has_video:
                v = info.video
                w, h = v.display_size
                parts.append(f"{label}: {w}×{h} {v.codec} {v.fps:.3g} fps · {_size(info.size)} · "
                             f"{_fmt(info.duration)}")
        if ri is not None and di is not None and ri.size:
            parts.append(f"the copy is {di.size / ri.size * 100:.0f}% of the original's size")
        self.info_lbl.config(text="\n".join(parts) or "Choose the original and the encoded copy to compare.")
        self.ab.load(ref if ri else None, dist if di else None, ri.duration if ri else 0, di.duration if di else 0)

    def _skips_changed(self):
        try:
            self.ab.skip_ref = max(0.0, float(self.skip_ref_var.get() or 0))
            self.ab.skip_dist = max(0.0, float(self.skip_dist_var.get() or 0))
        except ValueError:
            return
        self.ab.show(self.ab.pos.get())

    # ── Measure ───────────────────────────────────────────────────────
    def _model(self) -> str:
        return next((k for k, v in q.MODELS.items() if v[0] == self.model_var.get()), "hd")

    def _run(self):
        ref, dist = self.ref_row.path, self.dist_row.path
        ri = common.check_source(self, ref, what="Original")
        if ri is None:
            return
        di = common.check_source(self, dist, what="Encoded copy")
        if di is None:
            return
        try:
            skip_ref = common.number(self.skip_ref_var.get() or "0", "Skip (original)", 0, ri.duration)
            skip_dist = common.number(self.skip_dist_var.get() or "0", "Skip (encoded)", 0, di.duration)
            seconds = common.number(self.limit_var.get(), "Seconds", 0.5) if self.limit_on.get() else None
            argv, files = q.build(ref, dist, ri, di, metrics=[m for m, v in self.metric_vars.items() if v.get()],
                                  model=self._model(), skip_ref=skip_ref, skip_dist=skip_dist, seconds=seconds,
                                  subsample=next((k for k, v in SPEEDS.items() if v == self.speed_var.get()), 1))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        span = min(ri.duration - skip_ref, di.duration - skip_dist)
        span = min(span, seconds) if seconds else span
        fps = ri.video.fps or 30.0
        if ri.video.is_hdr or di.video.is_hdr:
            self.log(self.console, "Note: VMAF is made for normal (SDR) video - scores for HDR are only a rough guide.")

        def work(_p, _c, ctx):
            tmp = ctx.tempdir()
            ctx.log(f"Measuring {_fmt(span)} ({', '.join(files)})…")
            ctx.run(argv, duration=span, cwd=tmp)
            res = q.read(tmp, files, fps, skip_ref)
            self.call_ui(self._show, res)
            return 0

        common.queue_job(self, name="Compare & Quality", out="", work=work, btn=self.btn,
                         btn_label="📊  MEASURE QUALITY", console=self.console, duration=span, inputs=[ref, dist])

    def show_result(self, returncode, out_path=""):
        if returncode != 0:                 # success: the scores are shown in the page
            super().show_result(returncode, out_path)

    # ── Results ───────────────────────────────────────────────────────
    def _show(self, res: q.Result):
        self.result = res
        summary = res.summary()
        for m, (big, small) in self.score_lbls.items():
            s = summary.get(m)
            if not s:
                big.config(text="–")
                small.config(text="not measured")
                continue
            mean = s["mean"]
            if m == "ssim" and isinstance(mean, float):
                big.config(text=f"{mean:.4f}")
                small.config(text=f"{q.ssim_db(mean):.1f} dB · 1% low {s['low_1pct']:.4f}")
            elif mean == "inf":
                big.config(text="∞")
                small.config(text="identical pixels")
            elif isinstance(mean, float):
                unit = " dB" if m == "psnr" else ""
                big.config(text=f"{mean:.1f}{unit}")
                small.config(text=f"1% low {s['low_1pct']:.1f}{unit} · lowest {s['min']:.1f}{unit}")
        if "vmaf" in summary and isinstance(summary["vmaf"]["mean"], float):
            v = summary["vmaf"]["mean"]
            colour = CLR["green"] if v >= 90 else CLR["orange"] if v >= 70 else CLR["red"]
            self.verdict_lbl.config(text=f"{q.verdict(v)}  (VMAF {v:.1f})", fg=colour)
        else:
            self.verdict_lbl.config(text="", fg=CLR["fg"])
        for w in self.worst_frame.winfo_children():
            w.destroy()
        worst = res.worst(6, gap=max(1.0, (res.vmaf.times[-1] if res.vmaf.times else 10) / 30))
        if worst:
            tk.Label(self.worst_frame, text="Worst moments:").pack(side="left")
            for at, value in worst:
                tk.Button(self.worst_frame, text=f"{_fmt(at)}  ({value:.0f})", relief="flat", cursor="hand2",
                          command=lambda a=at: self.ab.show(a)).pack(side="left", padx=2)
            self.ab.show(worst[0][0])
        self._draw_graph()
        self.log(self.console, "Scores: " + json.dumps(summary))

    def _draw_graph(self):
        c = self.graph
        c.delete("all")
        res = self.result
        w, h = max(c.winfo_width(), 200), int(c["height"])
        series = res.vmaf if res and res.vmaf.values else (res.ssim if res and res.ssim.values else None)
        if series is None:
            c.create_text(w / 2, h / 2, text="The score over time appears here", fill="#777777", font=(UI_FONT, 10))
            return
        is_vmaf = series is res.vmaf
        lo, hi = (min(60.0, series.minimum() - 2), 100.0) if is_vmaf else (min(0.9, series.minimum()), 1.0)
        t0, t1 = series.times[0], series.times[-1] or 1.0
        pad_l, pad_r, pad_y = 34, 8, 8

        def xy(t, v):
            x = pad_l + (t - t0) / max(1e-6, t1 - t0) * (w - pad_l - pad_r)
            y = pad_y + (hi - min(hi, max(lo, v))) / (hi - lo) * (h - 2 * pad_y)
            return x, y
        for g in ((95, 90, 80, 70) if is_vmaf else (0.99, 0.98, 0.95)):
            if lo < g < hi:
                _x, y = xy(t0, g)
                c.create_line(pad_l, y, w - pad_r, y, fill="#333333", dash=(2, 3))
                c.create_text(pad_l - 4, y, text=f"{g:g}", fill="#888888", anchor="e", font=(UI_FONT, 8))
        pts = [xy(t, v) for t, v in zip(series.times, series.values) if math.isfinite(v)]
        if len(pts) > 1:
            step = max(1, len(pts) // 2000)
            c.create_line(*[coord for p in pts[::step] for coord in p], fill=CLR["accent"], width=1.5)
        c.create_text(w - pad_r, pad_y, text=("VMAF" if is_vmaf else "SSIM") + " per frame - click to look",
                      fill="#888888", anchor="ne", font=(UI_FONT, 8))
        self._graph_span = (t0, t1, pad_l, pad_r, w)

    def _graph_click(self, event):
        span = getattr(self, "_graph_span", None)
        if not span:
            return
        t0, t1, pad_l, pad_r, w = span
        frac = (event.x - pad_l) / max(1, w - pad_l - pad_r)
        self.ab.show(t0 + max(0.0, min(1.0, frac)) * (t1 - t0))

    def _export(self):
        if not self.result:
            messagebox.showinfo("Compare & Quality", "Measure first.", parent=self)
            return
        base = os.path.splitext(self.dist_row.path or "quality")[0] + " (quality)"
        path = dialogs.ask_save(base + ".csv", kind="any", defaultextension=".csv", parent=self)
        if not path:
            return
        self.result.to_csv(path)
        with open(os.path.splitext(path)[0] + ".json", "w", encoding="utf-8") as fh:
            json.dump({"original": self.ref_row.path, "copy": self.dist_row.path, "model": self._model(),
                       "summary": self.result.summary()}, fh, indent=2)
        self.log(self.console, f"Saved {os.path.basename(path)} and the .json summary")

