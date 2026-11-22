"""
tabs/cutting/beat_sync.py  -  Beat Sync Cutter

Build a music montage: pick a song and some clips, and the video cuts land
exactly on the beat.

Beat detection: FFmpeg turns the music into an energy envelope (200 Hz);
onsets are the rises in log-energy.  The tempo comes from the envelope's
autocorrelation (70-180 BPM, biased towards ~120 to avoid double/half
tempo), the phase from the best-aligned grid, and each beat is then nudged
to the nearest strong onset so live, drifting tempos are followed.
"Every hit" mode uses the onsets themselves (sensitivity = threshold).

Rendering: every segment is encoded on its own with frame counts taken
from the cumulative beat times (so there is no drift, however many cuts),
then the segments are joined losslessly and the song is laid underneath.
"""
from __future__ import annotations

import array
import math
import os
import random
import tkinter as tk
from tkinter import messagebox, ttk

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FileRow, scroll_body

ENV_RATE = 200            # energy envelope samples per second
ORDERS = {"sequential": "In order, continuing through each clip",
          "shuffle": "Shuffle clips and positions"}
SIZES = {"Same as the first clip": None, "1920×1080": (1920, 1080), "1080×1920 (vertical)": (1080, 1920),
         "1280×720": (1280, 720), "1080×1080 (square)": (1080, 1080)}


# ── beat detection (pure, tested) ────────────────────────────────────────────

def envelope(path: str, stream: int = 0, timeout: float = 600) -> list:
    """Energy envelope of the audio at ENV_RATE Hz."""
    cmd = [ff.ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path,
           "-map", f"0:a:{stream}", "-vn", "-sn",
           "-af", f"aformat=channel_layouts=mono,aresample=22050,highpass=f=30,"
                  f"aeval=val(0)*val(0),aresample={ENV_RATE}",
           "-f", "f32le", "-"]
    r = ff.capture(cmd, timeout=timeout, text=False)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("Could not read the music's audio.")
    data = array.array("f")
    data.frombytes(r.stdout[: len(r.stdout) // 4 * 4])
    return [max(0.0, v) for v in data]


def onset_strength(env: list) -> list:
    logs = [math.log10(v + 1e-9) for v in env]
    on = [0.0] * len(logs)
    for i in range(2, len(logs)):
        on[i] = max(0.0, logs[i] - max(logs[i - 1], logs[i - 2]) * 0.5 - logs[i - 2] * 0.5)
    # light smoothing (3 samples = 15 ms)
    return [(on[max(i - 1, 0)] + on[i] + on[min(i + 1, len(on) - 1)]) / 3 for i in range(len(on))]


def estimate_tempo(onset: list, lo_bpm: float = 70, hi_bpm: float = 180) -> float:
    """Beat period in envelope samples."""
    seg = onset[: ENV_RATE * 90]
    if len(seg) < ENV_RATE * 4:
        return ENV_RATE * 0.5
    mean = sum(seg) / len(seg)
    x = [v - mean for v in seg]
    best, best_lag = -1e18, ENV_RATE * 0.5
    for lag in range(int(ENV_RATE * 60 / hi_bpm), int(ENV_RATE * 60 / lo_bpm) + 1):
        r = sum(a * b for a, b in zip(x, x[lag:])) / (len(x) - lag)
        bpm = 60 * ENV_RATE / lag
        weight = math.exp(-0.5 * (math.log2(bpm / 120) / 0.9) ** 2)
        if r * weight > best:
            best, best_lag = r * weight, lag
    # sub-sample refinement with the neighbours (parabolic)
    def ac(lag):
        return sum(a * b for a, b in zip(x, x[lag:])) / (len(x) - lag)
    y0, y1, y2 = ac(best_lag - 1), ac(best_lag), ac(best_lag + 1)
    denom = y0 - 2 * y1 + y2
    shift = 0.5 * (y0 - y2) / denom if denom else 0.0
    return best_lag + max(-0.5, min(0.5, shift))


def beat_grid(onset: list, period: float) -> list:
    """Beat times (s): best phase, then each beat snapped to a nearby onset."""
    n = len(onset)
    if n == 0:
        return []
    best, phase = -1.0, 0
    for ph in range(int(period)):
        s = sum(onset[int(round(ph + k * period))] for k in range(int((n - ph) / period)))
        if s > best:
            best, phase = s, ph
    peak = sorted(onset)[int(len(onset) * 0.9)] if onset else 0
    beats, pos = [], float(phase)
    win = max(1, int(period * 0.12))
    while pos < n:
        i = int(round(pos))
        lo, hi = max(0, i - win), min(n, i + win + 1)
        j = max(range(lo, hi), key=lambda k: onset[k]) if hi > lo else i
        if onset[j] >= peak:
            pos = float(j)
        beats.append(pos / ENV_RATE)
        pos += period
    return beats


def onset_peaks(onset: list, sensitivity: float = 0.5, min_gap: float = 0.15) -> list:
    """Every hit: local maxima above an adaptive threshold."""
    if not onset:
        return []
    ordered = sorted(onset)
    median = ordered[len(ordered) // 2]
    high = ordered[int(len(ordered) * 0.98)]
    thr = median + (high - median) * (1.0 - sensitivity) * 0.8
    gap = int(min_gap * ENV_RATE)
    out, last = [], -gap
    for i in range(1, len(onset) - 1):
        if onset[i] >= thr and onset[i] >= onset[i - 1] and onset[i] > onset[i + 1] and i - last >= gap:
            out.append(i / ENV_RATE)
            last = i
    return out


def frame_plan(cuts, total: float, fps: float) -> list:
    """[(start_s, frames)] covering 0..total with cuts on the given times."""
    marks = [0.0] + [c for c in cuts if 0 < c < total] + [total]
    plan = []
    for a, b in zip(marks, marks[1:]):
        frames = int(round(b * fps)) - int(round(a * fps))
        if frames > 0:
            plan.append((a, frames))
    return plan


def assign_sources(plan, clips, order: str, fps: float, seed: int = 7) -> list:
    """[(clip_path, in_point, frames)] for each planned segment."""
    rng = random.Random(seed)
    out, idx, pos = [], 0, {c[0]: 0.0 for c in clips}
    for _start, frames in plan:
        seconds = frames / fps
        if order == "shuffle":
            path, dur = rng.choice(clips)
            start = rng.uniform(0, max(0.0, dur - seconds)) if dur > seconds else 0.0
        else:
            path, dur = clips[idx % len(clips)]
            start = pos[path]
            if start + seconds > dur:
                start = 0.0 if seconds >= dur else start % max(dur - seconds, 0.001)
            pos[path] = start + seconds
            idx += 1
        out.append((path, start, frames))
    return out


class BeatSyncCutterTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.clips: list = []
        self.beats: list = []
        self._music_dur = 0.0
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.beat_sync_cutter"), "Music montages that cut on the beat.",
                         icon="🥁")
        body = scroll_body(self)
        self.music_row = FileRow(body, "Music:", self.src_var, kind="media",
                                 on_change=lambda _p: self._analyse())
        self.music_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        bl = tk.LabelFrame(body, text="  Beats  ", padx=px(12), pady=px(6))
        bl.pack(fill="x", padx=px(20), pady=px(4))
        r0 = tk.Frame(bl)
        r0.pack(fill="x")
        self.mode_var = tk.StringVar(value="grid")
        tk.Radiobutton(r0, text="Beat grid (steady tempo)", value="grid", variable=self.mode_var,
                       command=self._analyse).pack(side="left")
        tk.Radiobutton(r0, text="Every hit", value="hits", variable=self.mode_var,
                       command=self._analyse).pack(side="left", padx=px(10))
        tk.Label(r0, text="Sensitivity:").pack(side="left", padx=px(10, 2))
        self.sens_var = tk.DoubleVar(value=0.5)
        tk.Scale(r0, variable=self.sens_var, from_=0.05, to=0.95, resolution=0.05, orient="horizontal",
                 length=px(120), command=lambda _v: self._schedule()).pack(side="left")
        self.bpm_lbl = tk.Label(bl, text="Choose a music file.", fg=CLR["accent"], font=(MONO_FONT, 10))
        self.bpm_lbl.pack(anchor="w", pady=px(4, 0))
        self.canvas = tk.Canvas(bl, height=px(60), bg=CLR["console_bg"], highlightthickness=0)
        self.canvas.pack(fill="x", pady=px(4))
        self.canvas.bind("<Configure>", lambda _e: self._draw())
        r1 = tk.Frame(bl)
        r1.pack(fill="x")
        tk.Label(r1, text="Cut every").pack(side="left")
        self.every_var = tk.StringVar(value="2")
        ttk.Combobox(r1, textvariable=self.every_var, values=["1", "2", "4", "8", "16"],
                     state="readonly", width=4).pack(side="left", padx=px(4))
        tk.Label(r1, text="beats").pack(side="left")
        tk.Button(r1, text="Save beat times…", relief="flat", command=self._save_beats).pack(
            side="right")

        cl = tk.LabelFrame(body, text="  Clips  ", padx=px(12), pady=px(6))
        cl.pack(fill="x", padx=px(20), pady=px(4))
        top = tk.Frame(cl)
        top.pack(fill="x")
        self.listbox = tk.Listbox(top, height=6)
        self.listbox.pack(side="left", fill="x", expand=True)
        bt = tk.Frame(top)
        bt.pack(side="left", padx=px(6))
        for text, cmd in (("➕ Add clips", self._add), ("🗑 Remove", self._remove), ("🧹 Clear", self._clear)):
            tk.Button(bt, text=text, relief="flat", width=12, command=cmd).pack(fill="x", pady=px(1))
        r2 = tk.Frame(cl)
        r2.pack(fill="x", pady=px(4, 0))
        tk.Label(r2, text="Order:").pack(side="left")
        self.order_var = tk.StringVar(value=ORDERS["sequential"])
        ttk.Combobox(r2, textvariable=self.order_var, values=list(ORDERS.values()), state="readonly",
                     width=36).pack(side="left", padx=px(4))
        tk.Label(r2, text="Size:").pack(side="left", padx=px(10, 2))
        self.size_var = tk.StringVar(value=next(iter(SIZES)))
        ttk.Combobox(r2, textvariable=self.size_var, values=list(SIZES), state="readonly",
                     width=22).pack(side="left")

        out_lf = tk.LabelFrame(body, text="  Output  ", padx=px(12), pady=px(8))
        out_lf.pack(fill="x", padx=px(20), pady=px(6))
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"))
        self.encoder.pack(anchor="w")
        self.out_row = FileRow(out_lf, t("common.output_file"), save=True,
                               suggest=lambda: self.suggest_output(self.music_row.path, "montage", ".mp4"))
        self.out_row.pack(fill="x", pady=px(8, 0))
        self.btn_render = tk.Button(body, text="🥁  BUILD MONTAGE", font=(UI_FONT, 12, "bold"),
                                    bg=CLR["green"], fg="white", width=24, cursor="hand2",
                                    command=self._render)
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=6)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._onset = None
        self._period = None
        self._job = None

    # ── clips ────────────────────────────────────────────────────────────────

    def _add(self):
        for p in self.pick_sources("video"):
            info = ff.try_probe(p)
            if info is not None and info.has_video and info.duration > 0.2:
                self.clips.append((p, info.duration))
                self.listbox.insert("end", f"{os.path.basename(p)}   ({ff.format_time(info.duration)})")

    def _remove(self):
        sel = self.listbox.curselection()
        if sel:
            del self.clips[sel[0]]
            self.listbox.delete(sel[0])

    def _clear(self):
        self.clips.clear()
        self.listbox.delete(0, "end")

    def load_file(self, path):
        info = ff.try_probe(path)
        if info is None:
            return False
        if info.has_video and self.music_row.path:
            self.clips.append((path, info.duration))
            self.listbox.insert("end", f"{os.path.basename(path)}   ({ff.format_time(info.duration)})")
        else:
            self.src_var.set(path)
        return True

    # ── analysis ─────────────────────────────────────────────────────────────

    def _schedule(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._pick_beats)

    def _analyse(self):
        path = self.music_row.path
        info = ff.try_probe(path) if path else None
        if info is None or not info.has_audio:
            self.bpm_lbl.config(text="The music file needs an audio track.")
            return
        self._music_dur = info.duration
        self.bpm_lbl.config(text="Listening for the beat…")

        def work():
            on = onset_strength(envelope(path))
            return on, estimate_tempo(on)

        def done(res):
            self._onset, self._period = res
            self._pick_beats()

        def failed(exc):
            self.bpm_lbl.config(text=f"Beat detection failed: {exc}")

        from core import ui
        ui.run_in_thread(work, on_done=done, on_error=failed, name="BeatDetect")

    def _pick_beats(self):
        self._job = None
        if self._onset is None:
            return
        if self.mode_var.get() == "grid":
            self.beats = beat_grid(self._onset, self._period)
            bpm = 60 * ENV_RATE / self._period
            self.bpm_lbl.config(text=f"{bpm:.1f} BPM · {len(self.beats)} beats")
        else:
            self.beats = onset_peaks(self._onset, float(self.sens_var.get()))
            self.bpm_lbl.config(text=f"{len(self.beats)} hits")
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), px(100)), int(c.cget("height"))
        dur = self._music_dur or 1.0
        if self._onset:
            step = max(1, len(self._onset) // w)
            peak = max(self._onset) or 1
            for x in range(0, w):
                seg = self._onset[x * step:(x + 1) * step]
                v = (max(seg) if seg else 0) / peak
                c.create_line(x, h, x, h - v * (h - px(4)), fill="#335577")
        every = int(self.every_var.get() or 1)
        for i, b in enumerate(self.beats):
            x = b / dur * w
            c.create_line(x, 0, x, h, fill=CLR["accent"] if i % every == 0 else "#444444", width=px(1))

    def _save_beats(self):
        if not self.beats:
            return
        from core import dialogs
        out = dialogs.ask_save("beats.txt", kind="text", defaultextension=".txt", parent=self)
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("\n".join(f"{b:.3f}" for b in self.beats) + "\n")
            self.log_tagged(self.console, f"Saved {len(self.beats)} beat times to {out}", "success")

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self):
        music = self.music_row.path
        minfo = common.check_source(self, music, need_video=False, what="Music")
        if minfo is None:
            return
        if not self.beats:
            messagebox.showwarning(t("common.warning"), "No beats yet - wait for the analysis to "
                                                        "finish.", parent=self)
            return
        if not self.clips:
            messagebox.showwarning(t("common.warning"), "Add some video clips.", parent=self)
            return
        first = ff.probe(self.clips[0][0])
        size = SIZES.get(self.size_var.get())
        w, h = size if size else common.even_size(first)
        fps_str = common.frame_rate(first)
        num, _, den = fps_str.partition("/")
        fps = float(num) / float(den or 1)
        every = int(self.every_var.get() or 1)
        cuts = self.beats[::every]
        total = minfo.duration
        plan = frame_plan(cuts, total, fps)
        order = next((k for k, v in ORDERS.items() if v == self.order_var.get()), "sequential")
        segments = assign_sources(plan, self.clips, order, fps)
        out = self.confirm_output(self.out_row.path or self.suggest_output(music, "montage", ".mp4"),
                                  [music] + [c[0] for c in self.clips])
        if not out:
            return
        self.out_row.set(out)
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        chain = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                 f"fps={fps_str},format=yuv420p")
        n = len(segments)

        def work(_p, cancelled, ctx):
            tmp = ctx.tempdir()
            files = []
            for i, (path, start, frames) in enumerate(segments):
                seg = os.path.join(tmp, f"seg{i:05d}.mkv")
                ctx.span(0.95 * i / n, 0.95 * (i + 1) / n).run(
                    [ff.ffmpeg_path(), "-y", *venc.pre_input, "-stream_loop", "-1", "-ss", ff.ts(start),
                     "-i", path, "-frames:v", str(frames), "-an", "-sn",
                     *venc.vf_args(chain), *common.intermediate_args(venc), seg], duration=frames / fps)
                files.append(seg)
            lst = fl.write_concat_list(files, os.path.join(tmp, "list.txt"))
            ctx.span(0.95, 1.0).run(
                [ff.ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", music,
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 *enc.audio(enc.default_audio_for(container), container=container, bitrate="256k"),
                 "-t", f"{total:.3f}", *common.final_copy_args(venc, container),
                 ctx.output()], duration=total)
            return 0

        self.log(self.console, f"{len(plan)} cuts · {w}×{h} @ {fps_str} · {ff.format_time(total)}")
        common.queue_job(self, name="Beat montage", out=out, work=work, btn=self.btn_render,
                         btn_label="🥁  BUILD MONTAGE", console=self.console, duration=total,
                         inputs=[music] + [c[0] for c in self.clips])
