"""
tab_trimmer.py  ─  Quick Trimmer
Precise clip trimmer with a visual dual-handle canvas timeline,
inline ffplay preview, and lossless or re-encoded export.
"""

import tkinter as tk
from tkinter import messagebox
from core import dialogs
import subprocess
import os

from tabs.base_tab import BaseTab, CLR, UI_FONT, MONO_FONT, add_tooltip
from core.hardware import (    get_binary_path, launch_preview, CREATE_NO_WINDOW,
)
from core.i18n import t
from core import ffmpeg as ff
from core import ui as _ui
from tabs.widgets import FramePreview
from core.scaling import px
from tabs import framestep

def fmt_time(seconds):
    """Seconds -> MM:SS.mmm (or HH:MM:SS.mmm)."""
    return ff.format_time(seconds)


def parse_time(time_str):
    """HH:MM:SS.ms, MM:SS.ms, seconds (',' accepted as decimal point).
    Raises ValueError on anything else - never silently returns 0."""
    return ff.parse_time(time_str)


def keyframe_at_or_before(path, at, lookback=30.0):
    """PTS of the last video keyframe at or before *at* seconds (None if the
    probe fails).  Stream copy can only start on a keyframe."""
    ffprobe = ff.ffprobe_path()
    window = max(0.0, at - lookback)
    try:
        r = ff.capture([ffprobe, "-v", "error", "-read_intervals", f"{window:.3f}%{at + 0.05:.3f}",
                        "-select_streams", "v:0", "-skip_frame", "nokey",
                        "-show_entries", "frame=pts_time,best_effort_timestamp_time",
                        "-of", "csv=p=0", path], timeout=30)
    except Exception:
        return None
    best = None
    for line in r.stdout.splitlines():
        for part in line.split(","):
            try:
                pts = float(part)
            except ValueError:
                continue
            if pts <= at + 1e-3 and (best is None or pts > best):
                best = pts
            break
    if best is None and window > 0:
        return keyframe_at_or_before(path, at, lookback=lookback * 4) if lookback < 600 else 0.0
    return best if best is not None else 0.0


class TrimmerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path    = ""
        self.duration     = 0.0
        self.preview_proc = None
        
        # StringVars to hold human-readable times
        self._start_var   = tk.StringVar(value="00:00.00")
        self._end_var     = tk.StringVar(value="00:00.00")
        self._scrub       = tk.DoubleVar(value=0.0)
        
        self._copy_mode   = tk.BooleanVar(value=True)
        self._crf_var     = tk.StringVar(value="18")
        self._dragging    = None
        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self, bg=CLR["panel"])
        hdr.pack(fill="x")
        hdr_inner = tk.Frame(hdr, bg=CLR["panel"])
        hdr_inner.pack(fill="x", padx=px(20), pady=px(14, 13))
        tk.Label(hdr_inner, text="✂  " + t("tab.quick_trimmer"), font=(UI_FONT, 15, "bold"),
                 bg=CLR["panel"], fg=CLR["accent"]).pack(side="left")
        tk.Label(hdr_inner, text=t("trimmer.subtitle"),
                 font=(UI_FONT, 10), bg=CLR["panel"],
                 fg=CLR["fgdim"], anchor="w").pack(side="left", fill="x", expand=True, padx=px(16, 0))
        tk.Frame(self, bg=CLR["border"], height=px(1)).pack(fill="x")

        # Source row
        sf = tk.Frame(self); sf.pack(fill="x", padx=px(20), pady=px(14, 4))
        tk.Label(sf, text=t("common.source_video"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._src_var = tk.StringVar()
        src_entry = tk.Entry(sf, textvariable=self._src_var, width=62, relief="flat")
        src_entry.pack(side="left", padx=px(8))
        src_entry.bind("<Return>", lambda _e: self._load_typed_source())
        src_entry.bind("<FocusOut>", lambda _e: self._load_typed_source())
        tk.Button(sf, text=t("btn.browse"), command=self._browse, cursor="hand2", relief="flat").pack(side="left")

        # Canvas timeline
        tl_lf = tk.LabelFrame(self, text=t("trimmer.timeline_section"),
                               padx=px(8), pady=px(6))
        tl_lf.pack(fill="x", padx=px(20), pady=px(4))
        self._canvas = tk.Canvas(tl_lf, bg=CLR["console_bg"], height=px(110), highlightthickness=0)
        self._canvas.pack(fill="x")
        self._canvas.bind("<Configure>",     lambda _: self._draw())
        self._canvas.bind("<ButtonPress-1>",  self._on_press)
        self._canvas.bind("<B1-Motion>",      self._on_drag)
        self._canvas.bind("<ButtonRelease-1>",lambda _: self._release())

        # Info strip
        inf = tk.Frame(self); inf.pack(fill="x", padx=px(20), pady=px(2))
        self._info_lbl = tk.Label(inf, text=t("common.no_file_loaded"), fg=CLR["fgdim"],
                                  font=(MONO_FONT, 9))
        self._info_lbl.pack(side="left")
        self._dur_lbl = tk.Label(inf, text="", fg=CLR["accent"],
                                 font=(MONO_FONT, 9, "bold"))
        self._dur_lbl.pack(side="right")

        # Marker controls
        mk = tk.LabelFrame(self, text=t("trimmer.clip_boundaries_section"), padx=px(14), pady=px(8))
        mk.pack(fill="x", padx=px(20), pady=px(4))
        self.preview = FramePreview(mk, width=320, height=180)
        self.preview.pack(side="right", anchor="n", padx=px(12, 0))
        self._preview_soon = _ui.debounce(self, 90, lambda: self.preview.show(self._preview_t))
        self._preview_t = 0.0

        r1 = tk.Frame(mk); r1.pack(fill="x", pady=px(2))
        tk.Label(r1, text=t("trimmer.keep_from_label"), font=(UI_FONT, 10, "bold"), width=14, anchor="e").pack(side="left")
        self._start_ent = tk.Entry(r1, textvariable=self._start_var, width=12, font=(MONO_FONT, 10), relief="flat")
        self._start_ent.pack(side="left", padx=px(6))
        self._start_ent.bind("<Return>", self._apply_typed_times)
        self._start_ent.bind("<FocusOut>", self._apply_typed_times)
        
        tk.Button(r1, text=t("trimmer.set_to_preview_start"), bg=CLR["panel"], fg="white",
                  command=lambda: self._start_var.set(fmt_time(self._scrub.get())) or self._apply_typed_times()).pack(side="left", padx=px(4))
        tk.Label(r1, text=t("trimmer.start_handle_hint"),
                 fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(side="left")

        r2 = tk.Frame(mk); r2.pack(fill="x", pady=px(2))
        tk.Label(r2, text=t("trimmer.keep_until_label"), font=(UI_FONT, 10, "bold"), width=14, anchor="e").pack(side="left")
        self._end_ent = tk.Entry(r2, textvariable=self._end_var, width=12, font=(MONO_FONT, 10), relief="flat")
        self._end_ent.pack(side="left", padx=px(6))
        self._end_ent.bind("<Return>", self._apply_typed_times)
        self._end_ent.bind("<FocusOut>", self._apply_typed_times)
        
        tk.Button(r2, text=t("trimmer.set_to_preview_end"), bg=CLR["panel"], fg="white",
                  command=lambda: self._end_var.set(fmt_time(self._scrub.get())) or self._apply_typed_times()).pack(side="left", padx=px(4))
        tk.Label(r2, text=t("trimmer.end_handle_hint"),
                 fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(side="left")

        r3 = tk.Frame(mk); r3.pack(fill="x", pady=px(8))
        tk.Label(r3, text=t("trimmer.preview_time_label"), font=(UI_FONT, 10, "bold"), width=14, anchor="e").pack(side="left")
        # the button and time stay on the right; the slider takes whatever width is left
        tk.Button(r3, text=t("trimmer.show_frame_button"), bg=CLR["accent"], fg="white",
                  command=self._preview_at_scrub).pack(side="right", padx=px(8))
        self._scrub_lbl = tk.Label(r3, text="00:00.00", fg=CLR["accent"],
                                    font=(MONO_FONT, 10), width=9)
        self._scrub_lbl.pack(side="right")
        self._scrub_sc = tk.Scale(r3, variable=self._scrub, from_=0, to=100,
                                   resolution=0.001, orient="horizontal", length=px(200),
                                   command=self._on_scrub, highlightthickness=0)
        self._scrub_sc.pack(side="left", padx=px(6), fill="x", expand=True)
        # , and . step a frame (then [ Set to Preview Time ] or i / o for frame-exact cuts)
        framestep.slider(self._scrub_sc, lambda: framestep.fps_of(self.file_path))

        # Encode options
        enc = tk.LabelFrame(self, text=t("section.export_options"), padx=px(14), pady=px(6))
        enc.pack(fill="x", padx=px(20), pady=px(4))
        er = tk.Frame(enc); er.pack(fill="x")
        tk.Checkbutton(er, text=t("trimmer.stream_copy_checkbox"),
                       variable=self._copy_mode, font=(UI_FONT, 10),
                       command=self._toggle_enc).pack(side="left")
        self._enc_row = tk.Frame(enc)
        tk.Label(self._enc_row, text=t("common.crf")).pack(side="left")
        tk.Entry(self._enc_row, textvariable=self._crf_var, width=4, relief="flat").pack(side="left", padx=px(4))
        tk.Label(self._enc_row, text=t("trimmer.crf_hint"),
                 fg=CLR["fgdim"], font=(UI_FONT, 8)).pack(side="left")

        # Output + action buttons
        of = tk.Frame(self); of.pack(fill="x", padx=px(20), pady=px(6))
        tk.Label(of, text=t("common.output_file"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._out_var = tk.StringVar()
        tk.Entry(of, textvariable=self._out_var, width=62, relief="flat").pack(side="left", padx=px(8))
        tk.Button(of, text=t("common.save_as"), command=self._browse_out, cursor="hand2", relief="flat").pack(side="left")

        bf = tk.Frame(self); bf.pack(pady=px(8))
        prev_btn = tk.Button(bf, text=t("trimmer.preview_selected_button"), bg=CLR["accent"], fg="white",
                  width=28, command=self._preview_selection)
        prev_btn.pack(side="left", padx=px(8))
        add_tooltip(prev_btn, "Preview the selected region in ffplay (Space)")
        self._btn_trim = tk.Button(bf, text=t("trimmer.export_button"),
                                   font=(UI_FONT, 12, "bold"),
                                   bg=CLR["green"], fg="black",
                                   height=2, width=28,
                                   command=self._run_trim)
        self._btn_trim.pack(side="left", padx=px(8))
        add_tooltip(self._btn_trim, "Export the trimmed clip (Ctrl+S)")

        # Console
        cf = tk.Frame(self); cf.pack(fill="both", expand=True, padx=px(20), pady=px(4))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ─── File loading ─────────────────────────────────────────────────────
    def _browse(self):
        p = dialogs.askopenfilename(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mts *.m2ts *.ts *.flv *.wmv *.3gp"),
                       ("All", "*")])
        if p:
            self.load_file(p)

    def _load_typed_source(self):
        p = self._src_var.get().strip().strip('"')
        if p and p != self.file_path and os.path.isfile(p):
            self.load_file(p)

    def _default_out(self, src):
        ext = os.path.splitext(src)[1] if self._copy_mode.get() else ".mp4"
        return self.suggest_output(src, "trimmed", ext or ".mp4")

    def load_file(self, p):
        info = ff.try_probe(p)
        if info is None or not info.duration:
            messagebox.showerror(t("common.error"), t("common.no_input") + f"\n{p}", parent=self)
            return False
        self.file_path = p
        self._src_var.set(p)
        self.duration = info.video.duration if info.video and info.video.duration else info.duration
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

    def _browse_out(self):
        p = dialogs.asksaveasfilename(defaultextension=".mp4",
                                          filetypes=[("MP4", "*.mp4"), ("MKV", "*.mkv")])
        if p: self._out_var.set(p)

    # ─── Canvas timeline ──────────────────────────────────────────────────
    _HR = 9   # handle radius

    def _t2x(self, t, w):
        p = px(24)
        if self.duration <= 0: return p
        return p + int((t / self.duration) * (w - 2 * p))

    def _x2t(self, x, w):
        p = px(24)
        if self.duration <= 0: return 0.0
        return max(0.0, min(1.0, (x - p) / max(1, w - 2 * p))) * self.duration

    def _apply_typed_times(self, *_):
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

    def _tv(self, var, default=0.0):
        try:
            return parse_time(var.get())
        except ValueError:
            return default

    def _show_frame(self, at):
        self._preview_t = float(at)
        if self.file_path:
            self._preview_soon()

    def _draw(self, *_):
        c = self._canvas
        w = c.winfo_width(); h = c.winfo_height()
        if w < px(10): return
        
        try:
            c.delete("all")
            c.create_rectangle(0, 0, w, h, fill="#0D0D0D", outline="")
            if self.duration <= 0:
                c.create_text(w // 2, h // 2, text=t("trimmer.load_a_video_to_see_the_timeline"),
                              fill=CLR["fgdim"], font=(UI_FONT, 10)); return
            cy = h // 2
            hr = px(self._HR)
            s_sec = self._tv(self._start_var, 0.0)
            e_sec = self._tv(self._end_var, self.duration)
            
            xs = self._t2x(s_sec, w)
            xe = self._t2x(e_sec, w)
            xp = self._t2x(float(self._scrub.get()), w)
            
            # Track
            c.create_rectangle(px(24), cy - px(4), w - px(24), cy + px(4), fill="#2A2A2A", outline="")
            # Selection
            c.create_rectangle(xs, cy - px(4), xe, cy + px(4), fill="#1A5A98", outline="")
            
            # Tick marks
            for i in range(11):
                tx = self._t2x(self.duration * i / 10, w)
                c.create_line(tx, cy + px(6), tx, cy + px(14), fill="#444444", width=px(1))
                tick_time = self.duration * i / 10
                c.create_text(tx, cy + px(22), text=f"{int(tick_time//60):02d}:{int(tick_time%60):02d}",
                              fill="#444444", font=(UI_FONT, 7))
                              
            # Playhead (Vertical line + Triangle)
            # Draw line slicing through the track
            c.create_line(xp, cy - px(18), xp, cy + px(18), fill="#FFEB3B", width=px(2))
            # Draw downward triangle on top
            c.create_polygon(xp - px(7), cy - px(18), xp + px(7), cy - px(18), xp, cy - px(8),
                             fill="#FFEB3B", outline="#000000", width=px(1))
                             
            # Start handle (green)
            c.create_oval(xs - hr, cy - hr, xs + hr, cy + hr,
                          fill="#4CAF50", outline="white", width=px(2))
            c.create_text(xs, cy - px(20), text=f"START {fmt_time(s_sec)}",
                          fill="#4CAF50", font=(MONO_FONT, 8, "bold"))
                          
            # End handle (red)
            c.create_oval(xe - hr, cy - hr, xe + hr, cy + hr,
                          fill="#F44336", outline="white", width=px(2))
            c.create_text(xe, cy + px(36), text=f"END {fmt_time(e_sec)}",
                          fill="#F44336", font=(MONO_FONT, 8, "bold"))
                          
            # Selection duration header
            sel = max(0.0, e_sec - s_sec)
            c.create_text((xs+xe)//2, px(14), text=f"Clip Length: {fmt_time(sel)}",
                          fill="white", font=(UI_FONT, 9, "bold"))
        except Exception as e:
            self.log_debug(f"Canvas draw error: {e}")

    def _on_press(self, ev):
        w   = self._canvas.winfo_width()
        
        s_sec = self._tv(self._start_var, 0.0)
        e_sec = self._tv(self._end_var, self.duration)
        
        try:
            xs = self._t2x(s_sec, w)
            xe = self._t2x(e_sec, w)
            xp = self._t2x(float(self._scrub.get()), w)
        except (ValueError, tk.TclError):
            return
            
        def near(a, b): return abs(a - b) < px(14)
        
        if near(ev.x, xs):   self._dragging = "start"
        elif near(ev.x, xe): self._dragging = "end"
        elif near(ev.x, xp): self._dragging = "scrub"
        else:
            self._dragging = "scrub"
            self._scrub.set(self._x2t(ev.x, w))
            self._draw()

    def _on_drag(self, ev):
        if not self._dragging: return
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

    def _release(self):
        self._dragging = None

    def _update_dur(self):
        try:
            s_sec = self._tv(self._start_var, 0.0)
            e_sec = self._tv(self._end_var, self.duration)
            sel = max(0.0, e_sec - s_sec)
            self._dur_lbl.config(text=f"Clip Length: {fmt_time(sel)}")
        except Exception:
            pass

    def _on_scrub(self, val):
        self._show_frame(float(val))
        try:
            self._scrub_lbl.config(text=fmt_time(float(val)))
        except (ValueError, tk.TclError):
            pass
        self._draw()

    def _toggle_enc(self):
        if self._copy_mode.get():
            self._enc_row.pack_forget()
        else:
            self._enc_row.pack(fill="x", pady=px(4))

    # ─── Preview ─────────────────────────────────────────────────────────
    def _preview_at_scrub(self):
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if self.preview_proc:
            try: self.preview_proc.terminate()
            except Exception: pass
        self.preview_proc = launch_preview(self.file_path, start_time=self._scrub.get())

    def _preview_selection(self):
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"))
            return
        if self.preview_proc:
            try: self.preview_proc.terminate()
            except Exception: pass
            
        ss = self._tv(self._start_var, 0.0)
        ee = self._tv(self._end_var, self.duration)
        dur = max(0.1, ee - ss)
        
        ffplay = get_binary_path("ffplay.exe")
        cmd = [ffplay, "-ss", str(ss), "-i", self.file_path,
               "-t", str(dur), "-loop", "0",
               "-window_title", f"Trim Preview  {fmt_time(ss)} – {fmt_time(ee)}",
               "-x", "800", "-y", "450", "-loglevel", "quiet"]
        self.preview_proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)

    # ─── Trim / Export ────────────────────────────────────────────────────
    def _run_trim(self):
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        try:
            self.focus_set()
            self._apply_typed_times()
        except Exception:
            pass
        try:
            ss = parse_time(self._start_var.get())
            end = parse_time(self._end_var.get())
        except ValueError:
            messagebox.showerror(t("trimmer.invalid_range_error"), t("trimmer.invalid_range_message"), parent=self)
            return
        if end - ss <= 0:
            messagebox.showerror(t("trimmer.invalid_range_error"), t("trimmer.invalid_range_message"), parent=self)
            return
        out = self._out_var.get().strip() or self._default_out(self.file_path)
        default_ext = os.path.splitext(self.file_path)[1] if self._copy_mode.get() else ".mp4"
        out = self.confirm_output(out, [self.file_path], default_ext=default_ext or ".mp4")
        if not out:
            return
        self._out_var.set(out)
        try:
            self.console.delete("1.0", tk.END)
        except tk.TclError:
            pass

        ffmpeg = ff.ffmpeg_path()
        container = os.path.splitext(out)[1].lstrip(".").lower()
        if self._copy_mode.get():
            # Lossless: input-seek to the keyframe at/before the mark so video
            # and audio both start at 0 (output-side -ss with -c copy left a
            # second of missing video at the start).
            k = keyframe_at_or_before(self.file_path, ss)
            k = ss if k is None else k
            if ss - k > 0.01:
                self.log_tagged(self.console, t("trimmer.snapped_note") if t("trimmer.snapped_note") != "trimmer.snapped_note"
                                else f"\u2139 Lossless cuts must start on a keyframe: starting at {fmt_time(k)} "
                                     f"({ss - k:.2f} s before your mark). Untick 'stream copy' for a frame-exact cut.",
                                "info")
            dur = end - k
            cmd = [ffmpeg, "-y", "-ss", ff.ts(k), "-i", self.file_path, "-t", ff.ts(dur),
                   "-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-sn", "-dn",
                   "-map_metadata", "0", "-map_chapters", "-1"]
            if container in ("mp4", "m4v", "mov"):
                cmd += ["-movflags", "+faststart"]
            cmd.append(out)
            self.log(self.console, f"Lossless trim: {fmt_time(k)} \u2013 {fmt_time(end)}  ({fmt_time(dur)})")
        else:
            from core import encoders as enc
            venc = enc.video("h264", "high", speed="balanced", hw="off", container=container or "mp4")
            crf = (self._crf_var.get() or "18").strip()
            vargs = list(venc.args)
            if "-crf" in vargs and crf.replace(".", "", 1).isdigit():
                vargs[vargs.index("-crf") + 1] = crf
            dur = end - ss
            info = ff.try_probe(self.file_path)
            has_audio = bool(info and info.has_audio)
            cmd = [ffmpeg, "-y", "-ss", ff.ts(ss), "-i", self.file_path, "-t", ff.ts(dur),
                   "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn", *vargs,
                   *(enc.audio("aac", "192k", container=container or "mp4") if has_audio else []),
                   "-map_metadata", "0", "-map_chapters", "-1", out]
            self.log(self.console, f"Frame-accurate trim: {fmt_time(ss)} \u2013 {fmt_time(end)}  CRF={crf}")
        self.run_ffmpeg(cmd, self.console, on_done=lambda rc: self.show_result(rc, out),
                        btn=self._btn_trim, btn_label=t("trimmer.export_button"),
                        duration=dur, output=out, inputs=[self.file_path], name="Quick Trimmer")
