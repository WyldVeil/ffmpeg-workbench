"""
tab_trimmer.py  ─  Quick Trimmer
Precise clip trimmer with a visual dual-handle canvas timeline,
inline ffplay preview, and lossless or re-encoded export.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tkinter as tk
from functools import partial
from tkinter import messagebox

from core import ffmpeg as ff
from core import proc as _proc
from core import ui as _ui
from core.hardware import (
    launch_preview,
)
from core.i18n import t
from core.ops import trim
from core.scaling import px
from tabs import framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, add_tooltip, make_header
from tabs.kit import dialogs
from tabs.kit.contracts import ScrubTransport, Transport
from tabs.widgets import FramePreview, SaveRow

logger = logging.getLogger("crossfader.cutting.trimmer")


def fmt_time(seconds: float) -> str:
    """Seconds -> MM:SS.mmm (or HH:MM:SS.mmm)."""
    return ff.format_time(seconds)


def parse_time(time_str: str) -> float:
    """HH:MM:SS.ms, MM:SS.ms, seconds (',' accepted as decimal point).
    Raises ValueError on anything else - never silently returns 0."""
    return ff.parse_time(time_str)


class TrimmerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.file_path = ""
        self.duration = 0.0
        self.preview_proc = None

        # StringVars to hold human-readable times
        self._start_var = tk.StringVar(value="00:00.00")
        self._end_var = tk.StringVar(value="00:00.00")
        self._scrub = tk.DoubleVar(value=0.0)

        self._copy_mode = tk.BooleanVar(value=True)
        self._crf_var = tk.StringVar(value="18")
        self._dragging = None
        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────

    # ── Contracts (tabs/kit/contracts.py) ─────────────────────────────────
    def transport(self) -> Transport | None:
        return ScrubTransport(self._scrub, lambda: self.duration)

    def mark_in(self) -> None:
        self._start_var.set(fmt_time(self._scrub.get()))
        self._apply_typed_times()

    def mark_out(self) -> None:
        self._end_var.set(fmt_time(self._scrub.get()))
        self._apply_typed_times()

    def _build_ui(self) -> None:
        mk = self._build_src()
        r3 = self._build_start_ent(mk)
        bf, prev_btn = self._build_scrub(r3)
        self._build_render(bf, prev_btn)

    def _build_src(self) -> tk.LabelFrame:
        make_header(self, t("tab.quick_trimmer"), t("trimmer.subtitle"), icon="✂")

        # Source row
        sf = tk.Frame(self)
        sf.pack(fill="x", padx=px(20), pady=px(14, 4))
        tk.Label(sf, text=t("common.source_video"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._src_var = tk.StringVar()
        src_entry = tk.Entry(sf, textvariable=self._src_var, width=62, relief="flat")
        src_entry.pack(side="left", padx=px(8))
        src_entry.bind("<Return>", lambda _e: self._load_typed_source())
        src_entry.bind("<FocusOut>", lambda _e: self._load_typed_source())
        tk.Button(
            sf, text=t("btn.browse"), command=self._browse, cursor="hand2", relief="flat"
        ).pack(side="left")

        # Canvas timeline
        tl_lf = tk.LabelFrame(self, text=t("trimmer.timeline_section"), padx=px(8), pady=px(6))
        tl_lf.pack(fill="x", padx=px(20), pady=px(4))
        self._canvas = tk.Canvas(tl_lf, bg=CLR["console_bg"], height=px(110), highlightthickness=0)
        self._canvas.pack(fill="x")
        self._canvas.bind("<Configure>", lambda _: self._draw())
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", lambda _: self._release())

        # Info strip
        inf = tk.Frame(self)
        inf.pack(fill="x", padx=px(20), pady=px(2))
        self._info_lbl = tk.Label(
            inf, text=t("common.no_file_loaded"), fg=CLR["fgdim"], font=(MONO_FONT, 9)
        )
        self._info_lbl.pack(side="left")
        self._dur_lbl = tk.Label(inf, text="", fg=CLR["accent"], font=(MONO_FONT, 9, "bold"))
        self._dur_lbl.pack(side="right")

        # Marker controls
        mk = tk.LabelFrame(self, text=t("trimmer.clip_boundaries_section"), padx=px(14), pady=px(8))
        mk.pack(fill="x", padx=px(20), pady=px(4))
        self.preview = FramePreview(mk, width=320, height=180)
        self.preview.pack(side="right", anchor="n", padx=px(12, 0))
        self._preview_soon = _ui.debounce(self, 90, lambda: self.preview.show(self._preview_t))
        self._preview_t = 0.0
        return mk

    def _mark_here(self, var: tk.StringVar) -> None:
        """Set a mark (start or end) to the scrubber's position."""
        var.set(fmt_time(self._scrub.get()))
        self._apply_typed_times()

    def _build_start_ent(self, mk: tk.LabelFrame) -> tk.Frame:
        r1 = tk.Frame(mk)
        r1.pack(fill="x", pady=px(2))
        tk.Label(
            r1, text=t("trimmer.keep_from_label"), font=(UI_FONT, 10, "bold"), width=14, anchor="e"
        ).pack(side="left")
        self._start_ent = tk.Entry(
            r1, textvariable=self._start_var, width=12, font=(MONO_FONT, 10), relief="flat"
        )
        self._start_ent.pack(side="left", padx=px(6))
        self._start_ent.bind("<Return>", self._apply_typed_times)
        self._start_ent.bind("<FocusOut>", self._apply_typed_times)

        tk.Button(
            r1,
            text=t("trimmer.set_to_preview_start"),
            bg=CLR["panel"],
            fg="white",
            command=partial(self._mark_here, self._start_var),
        ).pack(side="left", padx=px(4))
        tk.Label(r1, text=t("trimmer.start_handle_hint"), fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(
            side="left"
        )

        r2 = tk.Frame(mk)
        r2.pack(fill="x", pady=px(2))
        tk.Label(
            r2, text=t("trimmer.keep_until_label"), font=(UI_FONT, 10, "bold"), width=14, anchor="e"
        ).pack(side="left")
        self._end_ent = tk.Entry(
            r2, textvariable=self._end_var, width=12, font=(MONO_FONT, 10), relief="flat"
        )
        self._end_ent.pack(side="left", padx=px(6))
        self._end_ent.bind("<Return>", self._apply_typed_times)
        self._end_ent.bind("<FocusOut>", self._apply_typed_times)

        tk.Button(
            r2,
            text=t("trimmer.set_to_preview_end"),
            bg=CLR["panel"],
            fg="white",
            command=partial(self._mark_here, self._end_var),
        ).pack(side="left", padx=px(4))
        tk.Label(r2, text=t("trimmer.end_handle_hint"), fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(
            side="left"
        )

        r3 = tk.Frame(mk)
        r3.pack(fill="x", pady=px(8))
        tk.Label(
            r3,
            text=t("trimmer.preview_time_label"),
            font=(UI_FONT, 10, "bold"),
            width=14,
            anchor="e",
        ).pack(side="left")
        # the button and time stay on the right; the slider takes whatever width is left
        tk.Button(
            r3,
            text=t("trimmer.show_frame_button"),
            bg=CLR["accent"],
            fg="white",
            command=self._preview_at_scrub,
        ).pack(side="right", padx=px(8))
        return r3

    def _build_scrub(self, r3: tk.Frame) -> tuple[tk.Frame, tk.Button]:
        self._scrub_lbl = tk.Label(
            r3, text="00:00.00", fg=CLR["accent"], font=(MONO_FONT, 10), width=9
        )
        self._scrub_lbl.pack(side="right")
        self._scrub_sc = tk.Scale(
            r3,
            variable=self._scrub,
            from_=0,
            to=100,
            resolution=0.001,
            orient="horizontal",
            length=px(200),
            command=self._on_scrub,
            highlightthickness=0,
        )
        self._scrub_sc.pack(side="left", padx=px(6), fill="x", expand=True)
        # , and . step a frame (then [ Set to Preview Time ] or i / o for frame-exact cuts)
        framestep.slider(self._scrub_sc, lambda: framestep.fps_of(self.file_path))

        # Encode options
        enc = tk.LabelFrame(self, text=t("section.export_options"), padx=px(14), pady=px(6))
        enc.pack(fill="x", padx=px(20), pady=px(4))
        er = tk.Frame(enc)
        er.pack(fill="x")
        tk.Checkbutton(
            er,
            text=t("trimmer.stream_copy_checkbox"),
            variable=self._copy_mode,
            font=(UI_FONT, 10),
            command=self._toggle_enc,
        ).pack(side="left")
        self._enc_row = tk.Frame(enc)
        tk.Label(self._enc_row, text=t("common.crf")).pack(side="left")
        tk.Entry(self._enc_row, textvariable=self._crf_var, width=4, relief="flat").pack(
            side="left", padx=px(4)
        )
        tk.Label(
            self._enc_row, text=t("trimmer.crf_hint"), fg=CLR["fgdim"], font=(UI_FONT, 8)
        ).pack(side="left")

        # Output + action buttons
        self._out_var = tk.StringVar()
        SaveRow(self, t("common.output_file"), self._out_var).pack(
            fill="x", padx=px(20), pady=px(6)
        )

        bf = tk.Frame(self)
        bf.pack(pady=px(8))
        prev_btn = tk.Button(
            bf,
            text=t("trimmer.preview_selected_button"),
            bg=CLR["accent"],
            fg="white",
            width=28,
            command=self._preview_selection,
        )
        return bf, prev_btn

    def _build_render(self, bf: tk.Frame, prev_btn: tk.Button) -> None:
        prev_btn.pack(side="left", padx=px(8))
        add_tooltip(prev_btn, t("trimmer.preview_the_selected_region_in_ffplay_space"))
        self.btn_render = self.make_render_btn(
            bf, t("trimmer.export_button"), self._run_trim, width=28
        )
        self.btn_render.pack(side="left", padx=px(8))
        add_tooltip(self.btn_render, t("trimmer.export_the_trimmed_clip_ctrl_s"))

        # Console
        self.console = self.make_console_block(self, height=5, padx=px(20), pady=px(4))

    # ─── File loading ─────────────────────────────────────────────────────
    def _browse(self) -> None:
        p = dialogs.askopenfilename(
            filetypes=[
                (
                    "Video",
                    "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mts *.m2ts *.ts *.flv *.wmv *.3gp",
                ),
                ("All", "*"),
            ]
        )
        if p:
            self.load_file(p)

    def _load_typed_source(self) -> None:
        p = self._src_var.get().strip().strip('"')
        if p and p != self.file_path and os.path.isfile(p):
            self.load_file(p)

    def _default_out(self, src: str) -> str:
        ext = os.path.splitext(src)[1] if self._copy_mode.get() else ".mp4"
        return self.suggest_output(src, "trimmed", ext or ".mp4")

    def load_file(self, path: str) -> bool:
        p = path
        info = ff.try_probe(p)
        if info is None or not info.duration:
            messagebox.showerror(t("common.error"), t("common.no_input") + f"\n{p}", parent=self)
            return False
        self.file_path = p
        self._src_var.set(p)
        self.duration = (
            info.require_video().duration
            if info.video and info.require_video().duration
            else info.duration
        )
        self._out_var.set(self._default_out(p))
        self._start_var.set(fmt_time(0))
        self._end_var.set(fmt_time(self.duration))
        self._scrub.set(0.0)
        self._scrub_sc.config(to=max(self.duration, 1))
        self._info_lbl.config(text=f"{os.path.basename(p)}  \u00b7  {info.summary()}")
        self._update_dur()
        self._draw()
        self.preview.load(p, duration=self.duration, at=0.0)
        return True

    # ─── Canvas timeline ──────────────────────────────────────────────────
    _HR = 9  # handle radius

    def _t2x(self, t: float, w: int) -> int:
        p = px(24)
        if self.duration <= 0:
            return p
        return p + int((t / self.duration) * (w - 2 * p))

    def _x2t(self, x: float, w: int) -> float:
        p = px(24)
        if self.duration <= 0:
            return 0.0
        return max(0.0, min(1.0, (x - p) / max(1, w - 2 * p))) * self.duration

    def _apply_typed_times(self, *_: object) -> None:
        """Called when user types a time and hits Enter or clicks away."""
        try:
            s_sec = parse_time(self._start_var.get())
            e_sec = parse_time(self._end_var.get())
        except ValueError:
            self.log_tagged(self.console, t("trimmer.invalid_range_message"), "warn")
            self._start_var.set(fmt_time(getattr(self, "_last_start", 0.0)))
            self._end_var.set(fmt_time(getattr(self, "_last_end", self.duration)))
            return
        s_sec = max(0.0, min(s_sec, max(0.0, self.duration - 0.04)))
        e_sec = max(s_sec + 0.04, min(e_sec, self.duration)) if self.duration else e_sec
        self._last_start, self._last_end = s_sec, e_sec
        self._start_var.set(fmt_time(s_sec))
        self._end_var.set(fmt_time(e_sec))
        self._scrub.set(s_sec)
        self._scrub_lbl.config(text=fmt_time(s_sec))
        self._show_frame(s_sec)
        self._update_dur()
        self._draw()

    def _tv(self, var: tk.StringVar, default: float = 0.0) -> float:
        try:
            return parse_time(var.get())
        except ValueError:
            return default

    def _show_frame(self, at: float) -> None:
        self._preview_t = float(at)
        if self.file_path:
            self._preview_soon()

    def _draw(self, *_: object) -> None:
        c = self._canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < px(10):
            return
        try:
            c.delete("all")
            c.create_rectangle(0, 0, w, h, fill="#0D0D0D", outline="")
            if self.duration <= 0:
                c.create_text(
                    w // 2,
                    h // 2,
                    text=t("trimmer.load_a_video_to_see_the_timeline"),
                    fill=CLR["fgdim"],
                    font=(UI_FONT, 10),
                )
                return
            s_sec = self._tv(self._start_var, 0.0)
            e_sec = self._tv(self._end_var, self.duration)
            xs, xe = self._t2x(s_sec, w), self._t2x(e_sec, w)
            self._draw_track(w, h // 2, xs, xe)
            self._draw_playhead(self._t2x(float(self._scrub.get()), w), h // 2)
            self._draw_handles(h // 2, (xs, s_sec), (xe, e_sec))
        except Exception as e:
            logger.debug("_draw: best-effort step failed", exc_info=True)
            self.log_debug(f"Canvas draw error: {e}")

    def _draw_track(self, w: int, cy: int, xs: int, xe: int) -> None:
        """The track, the selection on it, and a tick (with its time) every tenth."""
        c = self._canvas
        c.create_rectangle(px(24), cy - px(4), w - px(24), cy + px(4), fill="#2A2A2A", outline="")
        c.create_rectangle(xs, cy - px(4), xe, cy + px(4), fill="#1A5A98", outline="")
        for i in range(11):
            tick_time = self.duration * i / 10
            tx = self._t2x(tick_time, w)
            c.create_line(tx, cy + px(6), tx, cy + px(14), fill="#444444", width=px(1))
            c.create_text(
                tx,
                cy + px(22),
                text=f"{int(tick_time // 60):02d}:{int(tick_time % 60):02d}",
                fill="#444444",
                font=(UI_FONT, 7),
            )

    def _draw_playhead(self, xp: int, cy: int) -> None:
        """A yellow line through the track, with a triangle on top."""
        c = self._canvas
        c.create_line(xp, cy - px(18), xp, cy + px(18), fill="#FFEB3B", width=px(2))
        c.create_polygon(
            xp - px(7),
            cy - px(18),
            xp + px(7),
            cy - px(18),
            xp,
            cy - px(8),
            fill="#FFEB3B",
            outline="#000000",
            width=px(1),
        )

    def _draw_handles(self, cy: int, start: tuple[int, float], end: tuple[int, float]) -> None:
        """The start (green) and end (red) handles with their times, and the length above."""
        c = self._canvas
        hr = px(self._HR)
        (xs, s_sec), (xe, e_sec) = start, end
        c.create_oval(
            xs - hr, cy - hr, xs + hr, cy + hr, fill="#4CAF50", outline="white", width=px(2)
        )
        c.create_text(
            xs,
            cy - px(20),
            text=t("trimmer.start_s_sec", s_sec=fmt_time(s_sec)),
            fill="#4CAF50",
            font=(MONO_FONT, 8, "bold"),
        )
        c.create_oval(
            xe - hr, cy - hr, xe + hr, cy + hr, fill="#F44336", outline="white", width=px(2)
        )
        c.create_text(
            xe,
            cy + px(36),
            text=t("trimmer.end_e_sec", e_sec=fmt_time(e_sec)),
            fill="#F44336",
            font=(MONO_FONT, 8, "bold"),
        )
        c.create_text(
            (xs + xe) // 2,
            px(14),
            text=t("trimmer.clip_length_value", value=fmt_time(max(0.0, e_sec - s_sec))),
            fill="white",
            font=(UI_FONT, 9, "bold"),
        )

    def _on_press(self, ev: tk.Event[tk.Misc]) -> None:
        w = self._canvas.winfo_width()

        s_sec = self._tv(self._start_var, 0.0)
        e_sec = self._tv(self._end_var, self.duration)

        try:
            xs = self._t2x(s_sec, w)
            xe = self._t2x(e_sec, w)
            xp = self._t2x(float(self._scrub.get()), w)
        except (ValueError, tk.TclError):
            return

        def near(a: float, b: float) -> bool:
            return abs(a - b) < px(14)

        if near(ev.x, xs):
            self._dragging = "start"
        elif near(ev.x, xe):
            self._dragging = "end"
        elif near(ev.x, xp):
            self._dragging = "scrub"
        else:
            self._dragging = "scrub"
            self._scrub.set(self._x2t(ev.x, w))
            self._draw()

    def _on_drag(self, ev: tk.Event[tk.Misc]) -> None:
        if not self._dragging:
            return
        w = self._canvas.winfo_width()
        t = self._x2t(ev.x, w)

        s_sec = self._tv(self._start_var, 0.0)
        e_sec = self._tv(self._end_var, self.duration)

        if self._dragging == "start":
            t = max(0.0, min(t, e_sec - 0.1))
            self._start_var.set(fmt_time(t))
        elif self._dragging == "end":
            t = max(s_sec + 0.1, min(t, self.duration))
            self._end_var.set(fmt_time(t))
        elif self._dragging == "scrub":
            t = max(0.0, min(t, self.duration))
            self._scrub.set(t)
            self._scrub_lbl.config(text=fmt_time(t))
        self._show_frame(t)

        self._update_dur()
        self._draw()

    def _release(self) -> None:
        self._dragging = None

    def _update_dur(self) -> None:
        try:
            s_sec = self._tv(self._start_var, 0.0)
            e_sec = self._tv(self._end_var, self.duration)
            sel = max(0.0, e_sec - s_sec)
            self._dur_lbl.config(text=t("trimmer.clip_length_sel", sel=fmt_time(sel)))
        except Exception:
            logger.debug("_update_dur: best-effort step failed", exc_info=True)

    def _on_scrub(self, val: str) -> None:
        self._show_frame(float(val))
        with contextlib.suppress(ValueError, tk.TclError):
            self._scrub_lbl.config(text=fmt_time(float(val)))
        self._draw()

    def _toggle_enc(self) -> None:
        if self._copy_mode.get():
            self._enc_row.pack_forget()
        else:
            self._enc_row.pack(fill="x", pady=px(4))

    # ─── Preview ─────────────────────────────────────────────────────────
    def _preview_at_scrub(self) -> None:
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if self.preview_proc:
            try:
                self.preview_proc.terminate()
            except Exception:
                logger.debug("_preview_at_scrub: best-effort step failed", exc_info=True)
        self.preview_proc = launch_preview(self.file_path, start_time=self._scrub.get())

    def _preview_selection(self) -> None:
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if self.preview_proc:
            try:
                self.preview_proc.terminate()
            except Exception:
                logger.debug("_preview_selection: best-effort step failed", exc_info=True)

        ss = self._tv(self._start_var, 0.0)
        ee = self._tv(self._end_var, self.duration)

        cmd = trim.preview_cmd(
            self.file_path, ss, ee, f"Trim Preview  {fmt_time(ss)} – {fmt_time(ee)}"
        )
        self.preview_proc = _proc.spawn(cmd)

    # ─── Trim / Export ────────────────────────────────────────────────────
    def _marks(self) -> tuple[float, float] | None:
        """(start, end) of the part to keep; None after saying the marks are wrong."""
        try:
            ss, end = parse_time(self._start_var.get()), parse_time(self._end_var.get())
        except ValueError:
            ss, end = 0.0, 0.0
        if end - ss <= 0:
            messagebox.showerror(
                t("trimmer.invalid_range_error"), t("trimmer.invalid_range_message"), parent=self
            )
            return None
        return ss, end

    def _copy_cmd(self, ss: float, end: float, out: str, container: str) -> tuple[list[str], float]:
        """Lossless (stream copy) from the keyframe at or before *ss*."""
        k = trim.keyframe_at_or_before(self.file_path, ss)
        k = ss if k is None else k
        if ss - k > 0.01:
            self.log_tagged(
                self.console,
                t("trimmer.snapped_note")
                if t("trimmer.snapped_note") != "trimmer.snapped_note"
                else f"\u2139 Lossless cuts must start on a keyframe: starting at {fmt_time(k)} "
                f"({ss - k:.2f} s before your mark). Untick 'stream copy' for a frame-exact cut.",
                "info",
            )
        self.log(
            self.console,
            t(
                "trimmer.lossless_trim_k_end_end2",
                k=fmt_time(k),
                end=fmt_time(end),
                end2=fmt_time(end - k),
            ),
        )
        return trim.copy_cmd(self.file_path, k, end, out, container), end - k

    def _encode_cmd(
        self, ss: float, end: float, out: str, container: str
    ) -> tuple[list[str], float]:
        """Frame-exact: re-encoded with H.264 at the chosen CRF."""
        crf = (self._crf_var.get() or "18").strip()
        info = ff.try_probe(self.file_path)
        cmd = trim.encode_cmd(
            self.file_path, ss, end, out, container, crf, has_audio=bool(info and info.has_audio)
        )
        self.log(
            self.console,
            t(
                "trimmer.frame_accurate_trim_ss_end_crf_crf",
                ss=fmt_time(ss),
                end=fmt_time(end),
                crf=crf,
            ),
        )
        return cmd, end - ss

    def _run_trim(self) -> None:
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        try:
            self.focus_set()
            self._apply_typed_times()
        except Exception:
            logger.debug("_run_trim: best-effort step failed", exc_info=True)
        marks = self._marks()
        if marks is None:
            return
        ss, end = marks
        out = self._out_var.get().strip() or self._default_out(self.file_path)
        default_ext = os.path.splitext(self.file_path)[1] if self._copy_mode.get() else ".mp4"
        out = self.confirm_output(out, [self.file_path], default_ext=default_ext or ".mp4")
        if not out:
            return
        self._out_var.set(out)
        with contextlib.suppress(tk.TclError):
            self.console.delete("1.0", tk.END)

        container = os.path.splitext(out)[1].lstrip(".").lower()
        build = self._copy_cmd if self._copy_mode.get() else self._encode_cmd
        cmd, dur = build(ss, end, out, container)
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=dur,
            output=out,
            inputs=[self.file_path],
            name="Quick Trimmer",
        )
