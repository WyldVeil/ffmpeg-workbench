"""
tabs/audio/waveform.py  -  Audio Waveform Editor

See a file's waveform, select parts of it and mute, boost or duck them.

* FFmpeg measures the peak level of every slice of the audio (at its own
  sample rate) in one pass - fast for long files, nothing decoded in Python.
* Drag on the waveform to select (or click = In, Shift+click = Out, or type
  the times); mouse wheel zooms around the pointer, Shift+wheel scrolls.
* Regions change the level with 20 ms ramps, so there are no clicks at
  their edges.  Export the whole video with the new sound, or audio only.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import tkinter as tk
from functools import partial
from tkinter import messagebox

from core import ffmpeg as ff
from core import tasks
from core.i18n import t
from core.ops.shared import AUDIO_ONLY_EXTS, audio_encoder_args
from core.scaling import px
from core.timecode import Timecode
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import FileRow

RAMP = 0.02  # seconds of fade at each region edge
COLOURS = {"mute": "red", "boost": "green", "duck": "orange"}


def _fmt(seconds):
    return f"{Timecode.of(seconds):.2}"


def peaks_cmd(src: str, slice_samples: int, out_txt: str, stream: int = 0) -> list:
    """Peak level (dBFS) of every *slice_samples* block, printed to *out_txt*."""
    af = (
        f"aformat=sample_fmts=flt:channel_layouts=mono,asetnsamples=n={slice_samples}:p=0,"
        "astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Peak_level,"
        f"ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file={os.path.basename(out_txt)}"
    )
    return [
        ff.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        src,
        "-map",
        f"0:a:{stream}",
        "-af",
        af,
        "-f",
        "null",
        "-",
    ]


def read_peaks(path: str) -> list:
    """dBFS lines -> linear peaks 0..1."""
    vals = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("lavfi.astats.Overall.Peak_level="):
                try:
                    db = float(line.split("=", 1)[1])
                except ValueError:
                    db = -120.0
                vals.append(0.0 if math.isinf(db) or db < -120 else min(1.0, 10 ** (db / 20)))
    return vals


def region_filter(regions) -> str:
    """Gain envelope for [(start, end, action, dB)] with RAMP-second edges."""
    parts = ["asetnsamples=n=256:p=0"] if regions else []
    for s, e, action, db in regions:
        gain = 0.0 if action == "mute" else 10 ** ((db if action == "boost" else -db) / 20)
        parts.append(
            f"volume='1+({gain:.6f}-1)*clip(min(t-{s:.3f},{e:.3f}-t)/{RAMP},0,1)':eval=frame"
        )
    return ",".join(parts)


class WaveformEditorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self.duration = 0.0
        self._info = None
        self._peaks = []  # linear 0..1, one per slice
        self._view = (0.0, 0.0)  # visible time range
        self._mark_in = 0.0
        self._mark_out = 0.0
        self._regions = []  # (start, end, action, dB)
        self._drag_from = None
        self._prev_out = 0.0
        self._load_id = 0
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.make_header(self, t("tab.waveform_editor"), t("waveform.subtitle"), icon="🎵")
        self.src_row = FileRow(self, t("common.source_file"), kind="media", on_change=self._load)
        self.src_row.pack(fill="x", padx=px(20), pady=px(12, 2))
        self._src_var = self.src_row.var
        self._info_lbl = tk.Label(self, text="", fg=CLR["fgdim"], font=(MONO_FONT, 9), anchor="w")
        self._info_lbl.pack(fill="x", padx=px(20))

        wf_lf = tk.LabelFrame(
            self,
            text=f"  {t('waveform.mark_info_section')}  ",
            padx=px(8),
            pady=px(6),
            font=(UI_FONT, 9, "bold"),
        )
        wf_lf.pack(fill="x", padx=px(20), pady=px(6))
        self._canvas = tk.Canvas(
            wf_lf, bg=CLR["console_bg"], height=px(160), highlightthickness=0, cursor="xterm"
        )
        self._canvas.pack(fill="x")
        c = self._canvas
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Shift-ButtonPress-1>", self._shift_click)
        c.bind("<Configure>", lambda _e: self._draw())
        wheel = [("<MouseWheel>", None)]
        if tk.TkVersion < 9:  # X11 wheel on Tk 8.6; Tk 9 uses 4/5 for the back/forward buttons
            wheel += [("<Button-4>", 1), ("<Button-5>", -1)]
        for seq, step in wheel:
            c.bind(seq, lambda e, s=step: self._wheel(e, s))
            c.bind(
                "<Shift-" + seq.strip("<>") + ">", lambda e, s=step: self._wheel(e, s, scroll=True)
            )
        tk.Label(
            wf_lf,
            text="Drag to select · click = In · Shift+click = Out · wheel = zoom · "
            "Shift+wheel = scroll",
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w")

        mk = tk.Frame(self)
        mk.pack(fill="x", padx=px(20), pady=px(4))
        self._in_var, self._out_var_t = tk.StringVar(value="0"), tk.StringVar(value="0")
        tk.Label(mk, text=t("waveform.in_label"), font=(UI_FONT, 10, "bold"), fg=CLR["green"]).pack(
            side="left"
        )
        e_in = tk.Entry(mk, textvariable=self._in_var, width=11, font=(MONO_FONT, 10))
        e_in.pack(side="left", padx=px(4, 14))
        tk.Label(mk, text=t("waveform.out_label"), font=(UI_FONT, 10, "bold"), fg=CLR["red"]).pack(
            side="left"
        )
        e_out = tk.Entry(mk, textvariable=self._out_var_t, width=11, font=(MONO_FONT, 10))
        e_out.pack(side="left", padx=px(4, 14))
        for ent in (e_in, e_out):
            ent.bind("<Return>", lambda _e: self._typed_marks())
            ent.bind("<FocusOut>", lambda _e: self._typed_marks())
        tk.Label(mk, text=t("waveform.selection_label"), font=(UI_FONT, 10)).pack(side="left")
        self._sel_lbl = tk.Label(mk, text="0.00s", font=(MONO_FONT, 10), fg=CLR["accent"], width=10)
        self._sel_lbl.pack(side="left")
        tk.Button(mk, text="Zoom to selection", relief="flat", command=self._zoom_sel).pack(
            side="left", padx=px(4)
        )
        tk.Button(mk, text="Show all", relief="flat", command=self._zoom_all).pack(side="left")

        act = tk.LabelFrame(
            self,
            text=f"  {t('waveform.region_actions_section')}  ",
            padx=px(15),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
        )
        act.pack(fill="x", padx=px(20), pady=px(6))
        for key, label in (
            ("mute", t("waveform.mute_button")),
            ("boost", t("waveform.boost_button")),
            ("duck", t("waveform.duck_button")),
        ):
            tk.Button(
                act,
                text=label,
                bg=CLR[COLOURS[key]],
                fg="white",
                font=(UI_FONT, 10, "bold"),
                width=16,
                cursor="hand2",
                command=lambda k=key: self._add_region(k),
            ).pack(side="left", padx=px(4))
        tk.Label(act, text=t("waveform.amount_label"), font=(UI_FONT, 10)).pack(
            side="left", padx=px(20, 0)
        )
        self._vol_adj = tk.StringVar(value="6")
        tk.Entry(act, textvariable=self._vol_adj, width=5, font=(UI_FONT, 10)).pack(
            side="left", padx=px(4)
        )
        tk.Label(act, text=t("waveform.db_label"), fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(
            side="left"
        )

        reg = tk.LabelFrame(
            self,
            text=f"  {t('waveform.applied_regions_section')}  ",
            padx=px(10),
            pady=px(6),
            font=(UI_FONT, 9, "bold"),
        )
        reg.pack(fill="x", padx=px(20), pady=px(4))
        self._region_list = tk.Listbox(
            reg,
            bg=CLR["console_bg"],
            fg=CLR["console_fg"],
            font=(MONO_FONT, 9),
            height=4,
            relief="flat",
            bd=0,
        )
        self._region_list.pack(fill="x", side="left", expand=True)
        rb = tk.Frame(reg)
        rb.pack(side="right", padx=px(6))
        tk.Button(rb, text=t("batch_joiner.btn_remove"), command=self._remove_region).pack(
            pady=px(2), fill="x"
        )
        tk.Button(rb, text=t("waveform.clear_all"), command=self._clear_regions).pack(
            pady=px(2), fill="x"
        )

        of = tk.Frame(self)
        of.pack(fill="x", padx=px(20), pady=px(6))
        self._export_mode = tk.StringVar(value="video")
        tk.Radiobutton(
            of,
            text=t("waveform.export_video_option"),
            variable=self._export_mode,
            value="video",
            command=self._mode_changed,
        ).pack(side="left", padx=px(0, 20))
        tk.Radiobutton(
            of,
            text=t("waveform.export_audio_option"),
            variable=self._export_mode,
            value="audio",
            command=self._mode_changed,
        ).pack(side="left")
        self.out_row = FileRow(
            self, t("common.output_file"), save=True, kind="media", suggest=lambda: self._suggest()
        )
        self.out_row.pack(fill="x", padx=px(20))
        self._out_var = self.out_row.var
        self.btn_render = tk.Button(
            self,
            text=t("waveform.apply_button"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            height=2,
            width=26,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(4, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _suggest(self):
        if not self.file_path:
            return ""
        audio = self._export_mode.get() == "audio" or not (self._info and self._info.has_video)
        return self.suggest_output(self.file_path, "edited", ".wav" if audio else None)

    def _mode_changed(self):
        if self.file_path:
            self.out_row.set(self._suggest())

    # ── Loading ───────────────────────────────────────────────────────
    def _load(self, path):
        """New file: forget everything about the previous one, then analyse."""
        self._load_id += 1
        my_id = self._load_id
        self.file_path, self.duration, self._info, self._peaks = path, 0.0, None, []
        self._regions.clear()
        self._region_list.delete(0, "end")
        self._mark_in = self._mark_out = 0.0
        self._view = (0.0, 0.0)
        self._info_lbl.config(text="Reading the file…")
        self._draw()
        tasks.submit(
            partial(self._analyse, path),
            on_done=lambda res: self._loaded(my_id, res),
            on_error=lambda e: self._failed(my_id, e),
            name="Waveform",
        )

    @staticmethod
    def _analyse(path):
        info = ff.probe(path)
        if not info.has_audio:
            raise ValueError("This file has no audio.")
        rate = info.require_audio().sample_rate or 48000
        dur = info.duration or 0.0
        slices = max(200, min(20000, int(dur * 100)))  # up to 10 ms resolution
        slice_samples = max(64, math.ceil(dur * rate / slices)) if dur else 4800
        tmp = tempfile.mkdtemp(prefix="wme_wave_")
        try:
            out = os.path.join(tmp, "peaks.txt")
            res = ff.run(peaks_cmd(path, slice_samples, out), cwd=tmp)
            if res.returncode != 0 or not os.path.isfile(out):
                raise RuntimeError(
                    "FFmpeg could not read the audio:\n" + "\n".join(res.log_tail[-4:])
                )
            peaks = read_peaks(out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return info, peaks, slice_samples / rate

    def _loaded(self, my_id, res):
        if my_id != self._load_id:
            return  # a newer file was chosen meanwhile
        info, peaks, slice_s = res
        self._info, self._peaks = info, peaks
        self.duration = info.duration or len(peaks) * slice_s
        self._mark_out = self.duration
        self._view = (0.0, self.duration)
        a = info.audio
        self._info_lbl.config(
            text=f"{_fmt(self.duration)}   {a.codec}  {a.sample_rate} Hz  "
            f"{a.channel_layout or str(a.channels) + ' ch'}"
        )
        self._sync_marks()
        if not self.out_row.path:
            self.out_row.set(self._suggest())
        self.log(self.console, f"Waveform ready: {len(peaks)} slices of {slice_s * 1000:.0f} ms")

    def _failed(self, my_id, exc):
        if my_id != self._load_id:
            return
        self.file_path = ""
        self._info_lbl.config(text="")
        self._draw()
        self.log(self.console, f"❌ {exc}")
        messagebox.showwarning(t("common.warning"), str(exc), parent=self)

    # ── Drawing ───────────────────────────────────────────────────────
    def _x(self, sec, w):
        a, b = self._view
        return (sec - a) / max(b - a, 1e-6) * w

    def _sec(self, x):
        a, b = self._view
        w = max(1, self._canvas.winfo_width())
        return max(0.0, min(self.duration, a + (x / w) * (b - a)))

    def _draw(self):
        c = self._canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < px(10):
            return
        mid = h // 2
        if not self._peaks or self.duration <= 0:
            c.create_text(
                w // 2,
                mid,
                text="Choose a file to see its waveform" if not self.file_path else "Reading…",
                fill=CLR["fgdim"],
                font=(UI_FONT, 11),
            )
            return
        a, b = self._view
        n = len(self._peaks)
        per = self.duration / n
        c.create_line(0, mid, w, mid, fill="#333333", width=px(1))
        for x in range(w):  # one column per pixel: max of its slices
            t0, t1 = a + (b - a) * x / w, a + (b - a) * (x + 1) / w
            i0, i1 = int(t0 / per), max(int(t0 / per) + 1, math.ceil(t1 / per))
            chunk = self._peaks[max(0, i0) : min(n, i1)]
            if not chunk:
                continue
            amp = int(max(chunk) * (mid - px(6)))
            colour = CLR["accent"]
            tm = (t0 + t1) / 2
            for rs, re_, act, _db in self._regions:
                if rs <= tm <= re_:
                    colour = CLR[COLOURS[act]]
                    break
            c.create_line(x, mid - amp, x, mid + amp + 1, fill=colour)
        x_in, x_out = self._x(self._mark_in, w), self._x(self._mark_out, w)
        if x_out > x_in:
            c.create_rectangle(x_in, 0, x_out, h, outline=CLR["accent"], dash=px(4, 2), width=px(1))
        c.create_line(x_in, 0, x_in, h, fill=CLR["green"], width=px(2))
        c.create_line(x_out, 0, x_out, h, fill=CLR["red"], width=px(2))
        for i in range(11):  # time ruler for the visible range
            tx = int(i * w / 10)
            c.create_line(tx, h - px(12), tx, h - px(4), fill="#555555", width=px(1))
            c.create_text(
                min(max(tx, px(24)), w - px(24)),
                h - px(2),
                text=_fmt(a + (b - a) * i / 10),
                fill="#666666",
                font=(UI_FONT, 6),
                anchor="s",
            )

    # ── Mouse / marks ─────────────────────────────────────────────────
    def _press(self, ev):
        if self.duration <= 0:
            return
        self._drag_from = self._sec(ev.x)
        self._prev_out = self._mark_out

    def _drag(self, ev):
        if self._drag_from is None:
            return
        cur = self._sec(ev.x)
        self._mark_in, self._mark_out = min(self._drag_from, cur), max(self._drag_from, cur)
        self._sync_marks()

    def _release(self, ev):
        if self._drag_from is None:
            return
        if abs(self._sec(ev.x) - self._drag_from) < 1e-3:  # a plain click moves In only
            self._mark_in = self._drag_from
            self._mark_out = self._prev_out if self._prev_out > self._mark_in else self.duration
            self._sync_marks()
        self._drag_from = None

    def _shift_click(self, ev):
        if self.duration > 0:
            self._mark_out = self._sec(ev.x)
            if self._mark_out < self._mark_in:
                self._mark_in, self._mark_out = self._mark_out, self._mark_in
            self._sync_marks()
        return "break"

    def _typed_marks(self):
        try:
            a = ff.parse_time(self._in_var.get() or "0")
            b = ff.parse_time(self._out_var_t.get() or "0")
        except ValueError:
            self._sync_marks()
            return
        a, b = max(0.0, min(a, self.duration)), max(0.0, min(b, self.duration))
        self._mark_in, self._mark_out = min(a, b), max(a, b)
        self._sync_marks()

    def _sync_marks(self):
        self._in_var.set(_fmt(self._mark_in))
        self._out_var_t.set(_fmt(self._mark_out))
        self._sel_lbl.config(text=f"{self._mark_out - self._mark_in:.2f}s")
        self._draw()

    def _wheel(self, ev, step, scroll=False):
        if self.duration <= 0:
            return "break"
        direction = step if step is not None else (1 if ev.delta > 0 else -1)
        a, b = self._view
        span = b - a
        if scroll:
            shift = -direction * span * 0.15
            a, b = a + shift, b + shift
        else:
            pivot = self._sec(ev.x)
            min_span = max(0.05, self.duration / max(1, len(self._peaks)) * 20)
            new = max(min_span, min(self.duration, span * (0.8 if direction > 0 else 1.25)))
            frac = (pivot - a) / span if span else 0.5
            a = pivot - frac * new
            b = a + new
        if a < 0:
            a, b = 0.0, b - a
        if b > self.duration:
            a, b = max(0.0, a - (b - self.duration)), self.duration
        self._view = (a, b)
        self._draw()
        return "break"

    def _zoom_sel(self):
        if self._mark_out - self._mark_in > 0.05:
            pad = (self._mark_out - self._mark_in) * 0.1
            self._view = (max(0.0, self._mark_in - pad), min(self.duration, self._mark_out + pad))
            self._draw()

    def _zoom_all(self):
        self._view = (0.0, self.duration)
        self._draw()

    # ── Regions ───────────────────────────────────────────────────────
    def _add_region(self, action):
        s, e = self._mark_in, self._mark_out
        if e - s < 0.01:
            messagebox.showwarning(
                t("waveform.no_selection_title"), t("waveform.no_selection_message"), parent=self
            )
            return
        db = 0.0
        if action != "mute":
            try:
                db = abs(float(self._vol_adj.get().replace("dB", "")))
            except ValueError:
                db = -1
            if not 0 < db <= 30:
                messagebox.showwarning(
                    t("common.warning"), "The amount must be between 0.1 and 30 dB.", parent=self
                )
                return
        self._regions.append((s, e, action, db))
        label = {"mute": "MUTE", "boost": f"BOOST +{db:g} dB", "duck": f"DUCK -{db:g} dB"}[action]
        self._region_list.insert("end", f"  {_fmt(s)} → {_fmt(e)}  [{label}]")
        self._draw()
        self.log(self.console, f"Region added: {label} at {_fmt(s)} → {_fmt(e)}")

    def _remove_region(self):
        sel = self._region_list.curselection()
        if sel and sel[0] < len(self._regions):
            self._region_list.delete(sel[0])
            self._regions.pop(sel[0])
            self._draw()

    def _clear_regions(self):
        self._regions.clear()
        self._region_list.delete(0, "end")
        self._draw()

    # ── Export ────────────────────────────────────────────────────────
    def _render(self):
        src, info = self.file_path, self._info
        if not src or info is None:
            messagebox.showwarning(t("waveform.no_file_title"), t("common.no_input"), parent=self)
            return
        if not self._regions:
            messagebox.showwarning(
                t("waveform.no_regions_title"), t("waveform.no_regions_message"), parent=self
            )
            return
        audio_only = self._export_mode.get() == "audio" or not info.has_video
        out = self.out_row.path or self._suggest()
        if audio_only and not out.lower().endswith(AUDIO_ONLY_EXTS):
            out = os.path.splitext(out)[0] + ".wav"
        out = self.confirm_output(out, [src], default_ext=".wav" if audio_only else ".mp4")
        if not out:
            return
        self.out_row.set(out)
        af = region_filter(self._regions)
        cmd = [ff.ffmpeg_path(), "-y", "-i", src]
        if audio_only:
            cmd += ["-map", "0:a:0", "-af", af, "-vn", *audio_encoder_args(out)]
        else:
            cmd += [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-af",
                af,
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
            ]
            if out.lower().endswith((".mp4", ".mov", ".m4v")):
                cmd += ["-movflags", "+faststart"]
        cmd.append(out)
        self.log(self.console, f"Applying {len(self._regions)} region(s)…")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=self.duration or None,
            output=out,
            inputs=[src],
            name="Waveform Editor",
        )
