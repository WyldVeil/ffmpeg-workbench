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

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import tasks
from core.beats import ENV_RATE, beat_grid, envelope, estimate_tempo, onset_peaks, onset_strength
from core.i18n import Choices, Labels, t
from core.ops import beat_montage as bm
from core.ops.shared import frame_rate
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, MONO_FONT, BaseTab
from tabs.kit.tktyped import selected_rows
from tabs.widgets import FileRow, output_section, scroll_body

ORDERS = Labels(
    "beat_sync.orders",
    {
        "sequential": "In order, continuing through each clip",
        "shuffle": "Shuffle clips and positions",
    },
)
SIZES = Choices(
    "beat_sync.sizes",
    {
        "Same as the first clip": None,
        "1920×1080": (1920, 1080),
        "1080×1920 (vertical)": (1080, 1920),
        "1280×720": (1280, 720),
        "1080×1080 (square)": (1080, 1080),
    },
)


class BeatSyncCutterTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self.clips: list[tuple[str, float]] = []
        self.beats: list[float] = []
        self._music_dur = 0.0
        self._build_ui()

    def _build_ui(self) -> None:
        body, cl = self._build_header()
        self._build_output(body, cl)

    def _build_header(self) -> tuple[tk.Frame, tk.LabelFrame]:
        self.make_header(self, t("tab.beat_sync_cutter"), t("toolinfo.beat_sync"), icon="🥁")
        body = scroll_body(self)
        self.music_row = FileRow(
            body,
            t("beat_sync.music"),
            self.src_var,
            kind="media",
            on_change=lambda _p: self._analyse(),
        )
        self.music_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        bl = tk.LabelFrame(body, text=t("beat_sync.beats"), padx=px(12), pady=px(6))
        bl.pack(fill="x", padx=px(20), pady=px(4))
        r0 = tk.Frame(bl)
        r0.pack(fill="x")
        self.mode_var = tk.StringVar(value="grid")
        tk.Radiobutton(
            r0,
            text=t("beat_sync.beat_grid_steady_tempo"),
            value="grid",
            variable=self.mode_var,
            command=self._analyse,
        ).pack(side="left")
        tk.Radiobutton(
            r0,
            text=t("beat_sync.every_hit"),
            value="hits",
            variable=self.mode_var,
            command=self._analyse,
        ).pack(side="left", padx=px(10))
        tk.Label(r0, text=t("beat_sync.lbl_sensitivity")).pack(side="left", padx=px(10, 2))
        self.sens_var = tk.DoubleVar(value=0.5)
        tk.Scale(
            r0,
            variable=self.sens_var,
            from_=0.05,
            to=0.95,
            resolution=0.05,
            orient="horizontal",
            length=px(120),
            command=lambda _v: self._schedule(),
        ).pack(side="left")
        self.bpm_lbl = tk.Label(
            bl, text=t("beat_sync.choose_a_music_file"), fg=CLR["accent"], font=(MONO_FONT, 10)
        )
        self.bpm_lbl.pack(anchor="w", pady=px(4, 0))
        self.canvas = tk.Canvas(bl, height=px(60), bg=CLR["console_bg"], highlightthickness=0)
        self.canvas.pack(fill="x", pady=px(4))
        self.canvas.bind("<Configure>", lambda _e: self._draw())
        r1 = tk.Frame(bl)
        r1.pack(fill="x")
        tk.Label(r1, text=t("beat_sync.cut_every")).pack(side="left")
        self.every_var = tk.StringVar(value="2")
        ttk.Combobox(
            r1,
            textvariable=self.every_var,
            values=["1", "2", "4", "8", "16"],
            state="readonly",
            width=4,
        ).pack(side="left", padx=px(4))
        tk.Label(r1, text="beats").pack(side="left")
        tk.Button(
            r1, text=t("beat_sync.save_beat_times"), relief="flat", command=self._save_beats
        ).pack(side="right")

        cl = tk.LabelFrame(body, text=t("beat_sync.clips"), padx=px(12), pady=px(6))
        cl.pack(fill="x", padx=px(20), pady=px(4))
        return body, cl

    def _build_output(self, body: tk.Frame, cl: tk.LabelFrame) -> None:
        top = tk.Frame(cl)
        top.pack(fill="x")
        self.listbox = tk.Listbox(top, height=6)
        self.listbox.pack(side="left", fill="x", expand=True)
        bt = tk.Frame(top)
        bt.pack(side="left", padx=px(6))
        for text, cmd in (
            ("➕ Add clips", self._add),
            ("🗑 Remove", self._remove),
            ("🧹 Clear", self._clear),
        ):
            tk.Button(bt, text=text, relief="flat", width=12, command=cmd).pack(
                fill="x", pady=px(1)
            )
        r2 = tk.Frame(cl)
        r2.pack(fill="x", pady=px(4, 0))
        tk.Label(r2, text=t("beat_sync.order")).pack(side="left")
        self.order_var = tk.StringVar(value=ORDERS["sequential"])
        ttk.Combobox(
            r2,
            textvariable=self.order_var,
            values=list(ORDERS.values()),
            state="readonly",
            width=36,
        ).pack(side="left", padx=px(4))
        tk.Label(r2, text=t("hard_subber.size_label")).pack(side="left", padx=px(10, 2))
        self.size_var = tk.StringVar(value=next(iter(SIZES)))
        ttk.Combobox(
            r2, textvariable=self.size_var, values=list(SIZES), state="readonly", width=22
        ).pack(side="left")

        self.encoder, self.out_row = output_section(
            body,
            t("common.output_file"),
            ("h264", "hevc", "av1"),
            suggest=lambda: self.suggest_output(self.music_row.path, "montage", ".mp4"),
        )
        self.btn_render = self.make_render_btn(
            body, t("beat_sync.build_montage"), self._render, width=24
        )
        self.btn_render.pack(pady=px(8))
        self.console = self.make_console_block(body, height=6)
        self._onset: list[float] | None = None
        self._period: float | None = None
        self._job: str | None = None

    # ── clips ────────────────────────────────────────────────────────────────

    def _add(self) -> None:
        for p in self.pick_sources("video"):
            info = ff.try_probe(p)
            if info is not None and info.has_video and info.duration > 0.2:
                self.clips.append((p, info.duration))
                self.listbox.insert(
                    "end", f"{os.path.basename(p)}   ({ff.format_time(info.duration)})"
                )

    def _remove(self) -> None:
        sel = selected_rows(self.listbox)
        if sel:
            del self.clips[sel[0]]
            self.listbox.delete(sel[0])

    def _clear(self) -> None:
        self.clips.clear()
        self.listbox.delete(0, "end")

    def load_file(self, path: str) -> bool:
        info = ff.try_probe(path)
        if info is None:
            return False
        if info.has_video and self.music_row.path:
            self.clips.append((path, info.duration))
            self.listbox.insert(
                "end", f"{os.path.basename(path)}   ({ff.format_time(info.duration)})"
            )
        else:
            self.src_var.set(path)
        return True

    # ── analysis ─────────────────────────────────────────────────────────────

    def _schedule(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._pick_beats)

    def _analyse(self) -> None:
        path = self.music_row.path
        info = ff.try_probe(path) if path else None
        if info is None or not info.has_audio:
            self.bpm_lbl.config(text=t("beat_sync.the_music_file_needs_an_audio_track"))
            return
        self._music_dur = info.duration
        self.bpm_lbl.config(text=t("beat_sync.listening_for_the_beat"))

        def work() -> tuple[list[float], float]:
            on = onset_strength(envelope(path))
            return on, estimate_tempo(on)

        def done(res: tuple[list[float], float]) -> None:
            self._onset, self._period = res
            self._pick_beats()

        def failed(exc: BaseException) -> None:
            self.bpm_lbl.config(text=t("beat_sync.beat_detection_failed_exc", exc=exc))

        tasks.submit(work, on_done=done, on_error=failed, name="BeatDetect")

    def _pick_beats(self) -> None:
        self._job = None
        if self._onset is None or self._period is None:
            return
        if self.mode_var.get() == "grid":
            self.beats = beat_grid(self._onset, self._period)
            bpm = 60 * ENV_RATE / self._period
            self.bpm_lbl.config(
                text=t("beat_sync.bpm_1f_bpm_beats_beats", bpm=bpm, beats=len(self.beats))
            )
        else:
            self.beats = onset_peaks(self._onset, float(self.sens_var.get()))
            self.bpm_lbl.config(text=t("beat_sync.beats_hits", beats=len(self.beats)))
        self._draw()

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), px(100)), int(c.cget("height"))
        dur = self._music_dur or 1.0
        if self._onset:
            step = max(1, len(self._onset) // w)
            peak = max(self._onset) or 1
            for x in range(0, w):
                seg = self._onset[x * step : (x + 1) * step]
                v = (max(seg) if seg else 0) / peak
                c.create_line(x, h, x, h - v * (h - px(4)), fill="#335577")
        every = int(self.every_var.get() or 1)
        for i, b in enumerate(self.beats):
            x = b / dur * w
            c.create_line(
                x, 0, x, h, fill=CLR["accent"] if i % every == 0 else "#444444", width=px(1)
            )

    def _save_beats(self) -> None:
        if not self.beats:
            return
        from tabs.kit import dialogs

        out = dialogs.ask_save("beats.txt", kind="text", defaultextension=".txt", parent=self)
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("\n".join(f"{b:.3f}" for b in self.beats) + "\n")
            self.log_tagged(
                self.console,
                t("beat_sync.saved_beats_beat_times_to_out", beats=len(self.beats), out=out),
                "success",
            )

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self) -> None:
        music = self.music_row.path
        minfo = common.check_source(self, music, need_video=False, what="Music")
        if minfo is None:
            return
        if not self.beats:
            messagebox.showwarning(
                t("common.warning"), t("beat_sync.no_beats_yet_wait_for_the_analysis"), parent=self
            )
            return
        if not self.clips:
            messagebox.showwarning(
                t("common.warning"), t("beat_sync.add_some_video_clips"), parent=self
            )
            return
        first = ff.probe(self.clips[0][0])
        size = SIZES.get(self.size_var.get())
        w, h = size if size else common.even_size(first)
        fps_str = frame_rate(first)
        num, _, den = fps_str.partition("/")
        fps = float(num) / float(den or 1)
        every = int(self.every_var.get() or 1)
        cuts = self.beats[::every]
        total = minfo.duration
        plan = bm.frame_plan(cuts, total, fps)
        order = next((k for k, v in ORDERS.items() if v == self.order_var.get()), "sequential")
        segments = bm.assign_sources(plan, self.clips, order, fps)
        out = self.confirm_output(
            self.out_row.path or self.suggest_output(music, "montage", ".mp4"),
            [music] + [c[0] for c in self.clips],
        )
        if not out:
            return
        self.out_row.set(out)
        container = common.container_of(out)
        job = bm.MontageJob(
            segments=segments,
            music=music,
            size=(w, h),
            rate=fps_str,
            fps=fps,
            duration=total,
            venc=self.encoder.video_encode(container),
            container=container,
        )

        self.log(
            self.console,
            t(
                "beat_sync.plan_cuts_w_h_fps_str_total",
                plan=len(plan),
                w=w,
                h=h,
                fps_str=fps_str,
                total=ff.format_time(total),
            ),
        )
        common.queue_job(
            self,
            common.Job(
                name="Beat montage",
                out=out,
                work=job.run,
                duration=total,
                inputs=[music] + [c[0] for c in self.clips],
            ),
            btn=self.btn_render,
            console=self.console,
        )
