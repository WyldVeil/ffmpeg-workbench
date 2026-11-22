"""
tabs/cutting/crossfader.py  -  Crossfader

Join up to 20 clips with smooth video transitions (xfade) and audio
crossfades (acrossfade) in a single FFmpeg pass.

* Clips of different sizes, frame rates, aspect ratios and sample rates are
  normalised to the first clip (letter/pillar-boxed, never stretched).
* Each clip is cut to its *video* length so audio can never drift, and clips
  without an audio track get matching silence instead of muting the result.
* "Overlap" transitions eat into both clips; "Freeze" holds the last/first
  frame so no action is lost.
* Any encoder the machine supports (x264/x265/SVT-AV1/VP9, NVENC/QSV/VAAPI/
  AMF/VideoToolbox) via the shared encoder panel; a quick low-resolution
  Preview renders the transitions before committing to the full render.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import dialogs
from core import encoders as enc
from core import ffmpeg as ff
from core.hardware import launch_preview
from core.i18n import t
from core.ops.crossfader import (
    TRANS_DEFAULT,
    Clip,
    _even,
    _tr,
    build_command,
    total_duration,
    validate,
)
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel

TRANSITIONS = [
    "fade",
    "dissolve",
    "fadeblack",
    "fadewhite",
    "fadegrays",
    "fadefast",
    "fadeslow",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "wipetl",
    "wipetr",
    "wipebl",
    "wipebr",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "coverleft",
    "coverright",
    "coverup",
    "coverdown",
    "revealleft",
    "revealright",
    "revealup",
    "revealdown",
    "circlecrop",
    "rectcrop",
    "circleopen",
    "circleclose",
    "vertopen",
    "vertclose",
    "horzopen",
    "horzclose",
    "diagtl",
    "diagtr",
    "diagbl",
    "diagbr",
    "hlslice",
    "hrslice",
    "vuslice",
    "vdslice",
    "hlwind",
    "hrwind",
    "vuwind",
    "vdwind",
    "radial",
    "pixelize",
    "distance",
    "hblur",
    "squeezeh",
    "squeezev",
    "zoomin",
]
AUDIO_CURVES = [
    "tri",
    "qsin",
    "hsin",
    "esin",
    "log",
    "ipar",
    "qua",
    "cub",
    "squ",
    "cbr",
    "par",
    "exp",
]
MAX_CLIPS = 20

AUDIO_MODES = [
    ("none", "crossfader.audio_none", "No Audio"),
    ("normal", "crossfader.audio_normal", "Normal Audio"),
    ("hq", "crossfader.audio_hq", "HQ Audio"),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Pure command building (unit-tested without any UI)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
#  Tab
# ─────────────────────────────────────────────────────────────────────────────


class CrossfaderTab(BaseTab):
    TOOL_NAME = "Crossfader"
    accepted_kinds = ("video",)

    def __init__(self, parent):
        super().__init__(parent)
        self.clip_rows: list = []  # dicts: frame, var, lbl, trans_var, trans_cb, num_lbl, clip
        self.audio_var = tk.StringVar()
        self.audio_curve_var = tk.StringVar(value="tri")
        self.trans_dur_var = tk.StringVar(value="1.0")
        self.trans_type_var = tk.StringVar(value=TRANS_DEFAULT)
        self.per_clip_trans = tk.BooleanVar(value=False)
        self.trans_behavior_var = tk.StringVar(value="overlap")
        self._audio_labels = {key: _tr(k, fb) for key, k, fb in AUDIO_MODES}
        self.audio_var.set(self._audio_labels["normal"])
        self._build_ui()

    # ── compatibility shims used by older code / tests ─────────────────────
    @property
    def clip_data(self) -> list:
        return [r["clip"] for r in self.clip_rows]

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.make_header(
            self, t("tab.crossfader"), t("crossfader.join_clips_with_smooth_transitions"), icon="✦"
        )
        body_canvas = tk.Canvas(self, highlightthickness=0, bg=CLR["bg"])
        body_sb = ttk.Scrollbar(self, orient="vertical", command=body_canvas.yview)
        self._inner = tk.Frame(body_canvas, bg=CLR["bg"])
        win = body_canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind(
            "<Configure>", lambda e: body_canvas.configure(scrollregion=body_canvas.bbox("all"))
        )
        body_canvas.bind("<Configure>", lambda e: body_canvas.itemconfigure(win, width=e.width))
        body_canvas.configure(yscrollcommand=body_sb.set)
        body_sb.pack(side="right", fill="y")
        body_canvas.pack(side="left", fill="both", expand=True)

        self._build_clips_section(self._inner)
        self._build_transition_section(self._inner)
        self._build_encoding_section(self._inner)
        self._build_output_section(self._inner)
        self._build_render_section(self._inner)

    def _build_clips_section(self, parent):
        lf = self.make_section(parent, "\U0001f3ac  " + t("section.clips"), padx=16)
        self._clips_frame = tk.Frame(lf, bg=CLR["bg"])
        self._clips_frame.pack(fill="x")
        btn_row = tk.Frame(lf, bg=CLR["bg"])
        btn_row.pack(fill="x", pady=px(6, 0))
        self.btn_add_clip = tk.Button(
            btn_row,
            text=t("crossfader.add_clip"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            relief="flat",
            cursor="hand2",
            command=self._add_clip_row,
        )
        self.btn_add_clip.pack(side="left", padx=px(4))
        tk.Button(
            btn_row,
            text=_tr("crossfader.add_many", "➕  Add several…"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            relief="flat",
            cursor="hand2",
            command=self._add_many,
        ).pack(side="left", padx=px(4))
        tk.Label(
            btn_row,
            text=_tr("crossfader.max_clips", "Up to {n} clips").format(n=MAX_CLIPS),
            fg=CLR["fgdim"],
            bg=CLR["bg"],
            font=(UI_FONT, 8),
        ).pack(side="left", padx=px(8))
        self._add_clip_row()
        self._add_clip_row()

    def _build_transition_section(self, parent):
        lf = self.make_section(parent, "\U0001f39e  " + t("section.transition"), padx=16)
        row1 = tk.Frame(lf, bg=CLR["bg"])
        row1.pack(fill="x", pady=px(2))
        tk.Label(
            row1,
            text=t("gif_maker.duration_label"),
            font=(UI_FONT, 10, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        tk.Spinbox(
            row1,
            from_=0.1,
            to=10,
            increment=0.1,
            format="%.1f",
            textvariable=self.trans_dur_var,
            width=6,
            font=(UI_FONT, 11),
        ).pack(side="left", padx=px(8))
        tk.Label(
            row1,
            text=t("crossfader.video_type"),
            font=(UI_FONT, 10, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left", padx=px(12, 0))
        self._global_trans_cb = ttk.Combobox(
            row1, textvariable=self.trans_type_var, values=TRANSITIONS, state="readonly", width=16
        )
        self._global_trans_cb.pack(side="left", padx=px(8))
        self.trans_type_var.trace_add("write", lambda *_: self._apply_global_trans())
        tk.Label(
            row1,
            text=t("crossfader.audio_curve"),
            font=(UI_FONT, 10, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left", padx=px(12, 0))
        ttk.Combobox(
            row1, textvariable=self.audio_curve_var, values=AUDIO_CURVES, state="readonly", width=8
        ).pack(side="left", padx=px(8))

        row2 = tk.Frame(lf, bg=CLR["bg"])
        row2.pack(fill="x", pady=px(4, 0))
        tk.Checkbutton(
            row2,
            text=t("crossfader.use_different_transition_for_each_clip_pair"),
            variable=self.per_clip_trans,
            command=self._toggle_per_clip_trans,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["input_bg"],
            activebackground=CLR["bg"],
            font=(UI_FONT, 10),
        ).pack(side="left")

        row3 = tk.Frame(lf, bg=CLR["bg"])
        row3.pack(fill="x", pady=px(6, 0))
        tk.Label(
            row3,
            text=t("crossfader.behavior"),
            font=(UI_FONT, 10, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        for value, key in (
            ("overlap", "crossfader.overlap_standard_merges_action_consumes_time"),
            ("freeze", "crossfader.freeze_frame_preserves_action_freezes_video_at_c"),
        ):
            tk.Radiobutton(
                row3,
                text=t(key),
                variable=self.trans_behavior_var,
                value=value,
                command=self._update_summary,
                bg=CLR["bg"],
                fg=CLR["fg"],
                selectcolor=CLR["input_bg"],
                activebackground=CLR["bg"],
                font=(UI_FONT, 10),
            ).pack(side="left", padx=px(8))
        self.trans_dur_var.trace_add("write", lambda *_: self._update_summary())

    def _build_encoding_section(self, parent):
        lf = self.make_section(parent, "⚙  " + t("section.encoding"), padx=16)
        self.encoder = EncoderPanel(
            lf, codecs=("h264", "hevc", "av1", "vp9"), default_quality="high"
        )
        self.encoder.pack(anchor="w")
        a_row = tk.Frame(lf, bg=CLR["bg"])
        a_row.pack(fill="x", pady=px(8, 0))
        tk.Label(
            a_row, text=t("crossfader.audio"), font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["fg"]
        ).pack(side="left")
        for key, _k, _fb in AUDIO_MODES:
            tk.Radiobutton(
                a_row,
                text=self._audio_labels[key],
                variable=self.audio_var,
                value=self._audio_labels[key],
                bg=CLR["bg"],
                fg=CLR["fg"],
                selectcolor=CLR["input_bg"],
                activebackground=CLR["bg"],
                font=(UI_FONT, 9),
            ).pack(side="left", padx=px(6))

    def _build_output_section(self, parent):
        lf = self.make_section(parent, "\U0001f4be  " + t("section.output"), padx=16)
        row = tk.Frame(lf, bg=CLR["bg"])
        row.pack(fill="x")
        tk.Label(
            row,
            text=t("crossfader.output_file"),
            font=(UI_FONT, 10, "bold"),
            bg=CLR["bg"],
            fg=CLR["fg"],
        ).pack(side="left")
        self.out_var = tk.StringVar()
        self.entry_out = tk.Entry(
            row,
            textvariable=self.out_var,
            width=70,
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            insertbackground=CLR["accent"],
        )
        self.entry_out.pack(side="left", padx=px(8), fill="x", expand=True)
        tk.Button(
            row,
            text=t("common.save_as"),
            command=self._browse_output,
            relief="flat",
            bg=CLR["panel"],
            fg=CLR["fg"],
        ).pack(side="left")

    def _build_render_section(self, parent):
        btn_row = tk.Frame(parent, bg=CLR["bg"])
        btn_row.pack(pady=px(10), padx=px(16), fill="x")
        self._render_label = t("crossfader.render_crossfade")
        self.run_btn = self.make_render_btn(
            btn_row, self._render_label, self._start_render, color=CLR["green"], width=26
        )
        self.run_btn.config(state="disabled")
        self.run_btn.pack(side="left", padx=px(6))
        self._preview_label = _tr("crossfader.preview_button", "\U0001f441  Quick preview")
        self.preview_btn = tk.Button(
            btn_row,
            text=self._preview_label,
            font=(UI_FONT, 10, "bold"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            relief="flat",
            cursor="hand2",
            state="disabled",
            command=self._start_preview,
        )
        self.preview_btn.pack(side="left", padx=px(6), ipady=px(8))
        self._status_lbl = tk.Label(
            btn_row, text="", fg=CLR["accent"], bg=CLR["bg"], font=(UI_FONT, 9), justify="left"
        )
        self._status_lbl.pack(side="left", padx=px(12))
        cf = tk.Frame(parent, bg=CLR["bg"])
        cf.pack(fill="both", expand=True, padx=px(16), pady=px(4))
        self.console, csb = self.make_console(cf, height=10)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── clip rows ────────────────────────────────────────────────────────────
    def _row_index(self, row) -> int:
        return self.clip_rows.index(row)

    def _add_clip_row(self, path: str = ""):
        if len(self.clip_rows) >= MAX_CLIPS:
            return None
        frame = tk.Frame(self._clips_frame, relief="groove", bd=px(1), bg=CLR["panel"])
        frame.pack(fill="x", pady=px(2))
        row = {"frame": frame, "clip": None}
        row["num_lbl"] = tk.Label(
            frame,
            text=str(len(self.clip_rows) + 1),
            width=3,
            font=(UI_FONT, 9, "bold"),
            fg=CLR["accent"],
            bg=CLR["panel"],
        )
        row["num_lbl"].pack(side="left", padx=px(4))
        row["var"] = tk.StringVar(value=path)
        entry = tk.Entry(
            frame,
            textvariable=row["var"],
            width=48,
            font=(MONO_FONT, 9),
            relief="flat",
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
        )
        entry.pack(side="left", padx=px(4), pady=px(3))
        entry.bind("<FocusOut>", lambda e, r=row: self._on_entry_update(r))
        entry.bind("<Return>", lambda e, r=row: self._on_entry_update(r))
        tk.Button(
            frame, text="…", width=3, relief="flat", command=lambda r=row: self._browse_clip(r)
        ).pack(side="left")
        row["lbl"] = tk.Label(
            frame,
            text=t("common.no_file_loaded"),
            fg=CLR["fgdim"],
            bg=CLR["panel"],
            font=(UI_FONT, 8),
            anchor="w",
        )
        row["lbl"].pack(side="left", padx=px(6), fill="x", expand=True)
        ctrl = tk.Frame(frame, bg=CLR["panel"])
        ctrl.pack(side="right", padx=px(4))
        row["ctrl"] = ctrl
        row["trans_var"] = tk.StringVar(value=self.trans_type_var.get())
        row["trans_cb"] = ttk.Combobox(
            frame, textvariable=row["trans_var"], values=TRANSITIONS, state="readonly", width=13
        )
        for txt, cmd in (
            ("↑", lambda r=row: self._move_clip(r, -1)),
            ("↓", lambda r=row: self._move_clip(r, +1)),
            ("✕", lambda r=row: self._remove_clip(r)),
        ):
            tk.Button(
                ctrl,
                text=txt,
                width=2,
                relief="flat",
                command=cmd,
                fg=CLR["red"] if txt == "✕" else CLR["fg"],
                bg=CLR["panel"],
            ).pack(side="left", padx=px(1))
        self.clip_rows.append(row)
        if len(self.clip_rows) >= MAX_CLIPS:
            self.btn_add_clip.config(state="disabled")
        self._toggle_per_clip_trans()
        if path:
            self._on_entry_update(row)
        self._validate()
        return row

    def _add_many(self):
        paths = dialogs.ask_open_many("video", title=t("crossfader.add_clip"), parent=self)
        for p in paths:
            self.load_file(p)

    def _remove_clip(self, row):
        if len(self.clip_rows) <= 2:
            row["var"].set("")
            row["clip"] = None
            row["lbl"].config(text=t("common.no_file_loaded"), fg=CLR["fgdim"])
            self._validate()
            return
        row["frame"].destroy()
        self.clip_rows.remove(row)
        self.btn_add_clip.config(state="normal")
        self._renumber()
        self._validate()

    def _move_clip(self, row, direction):
        idx = self._row_index(row)
        new = idx + direction
        if not 0 <= new < len(self.clip_rows):
            return
        self.clip_rows[idx], self.clip_rows[new] = self.clip_rows[new], self.clip_rows[idx]
        for r in self.clip_rows:
            r["frame"].pack_forget()
        for r in self.clip_rows:
            r["frame"].pack(fill="x", pady=px(2))
        self._renumber()
        self._validate()

    def _renumber(self):
        for i, r in enumerate(self.clip_rows):
            r["num_lbl"].config(text=str(i + 1))
        self._toggle_per_clip_trans()

    def _toggle_per_clip_trans(self):
        show = self.per_clip_trans.get()
        for i, r in enumerate(self.clip_rows):
            r["trans_cb"].pack_forget()
            if show and i > 0:
                r["trans_cb"].pack(side="right", padx=px(4), before=r["ctrl"])

    def _apply_global_trans(self):
        for r in self.clip_rows:
            r["trans_var"].set(self.trans_type_var.get())

    # ── files ────────────────────────────────────────────────────────────────
    def _browse_clip(self, row):
        path = dialogs.ask_open("video", title=t("crossfader.add_clip"), parent=self)
        if path:
            row["var"].set(path)
            self._on_entry_update(row)

    def load_file(self, path: str) -> bool:
        """Drag-and-drop / File > Open: fill the first empty row or add one."""
        for row in self.clip_rows:
            if not row["var"].get().strip():
                row["var"].set(path)
                self._on_entry_update(row)
                return True
        return self._add_clip_row(path) is not None

    def _on_entry_update(self, row):
        path = row["var"].get().strip()
        current = row["clip"].path if row["clip"] else ""
        if path == current:
            return
        row["clip"] = None
        if not path:
            row["lbl"].config(text=t("common.no_file_loaded"), fg=CLR["fgdim"])
        else:
            try:
                row["clip"] = Clip.from_info(ff.probe(path))
                row["lbl"].config(text=row["clip"].summary, fg=CLR["fgdim"])
            except (ff.ProbeError, ValueError) as exc:
                row["lbl"].config(text=str(exc)[:90], fg=CLR["red"])
        if row is self.clip_rows[0] and row["clip"] and not self.out_var.get().strip():
            self.out_var.set(self.suggest_output(path, "crossfaded", ".mp4"))
        self._validate()

    def _browse_output(self):
        current = self.out_var.get().strip()
        path = dialogs.ask_save(
            os.path.basename(current) or "crossfaded.mp4",
            kind="video",
            defaultextension=".mp4",
            parent=self,
            initialdir=os.path.dirname(current) or None,
        )
        if path:
            self.out_var.set(path)

    # ── state ────────────────────────────────────────────────────────────────
    def _clips(self) -> list:
        return [r["clip"] for r in self.clip_rows if r["clip"] is not None]

    def _transitions(self) -> list:
        rows = [r for r in self.clip_rows if r["clip"] is not None]
        if self.per_clip_trans.get():
            return [r["trans_var"].get() for r in rows[1:]]
        return [self.trans_type_var.get()] * max(0, len(rows) - 1)

    def _duration(self) -> float | None:
        try:
            return float(str(self.trans_dur_var.get()).replace(",", "."))
        except ValueError:
            return None

    def _audio_mode(self) -> str:
        rev = {v: k for k, v in self._audio_labels.items()}
        return rev.get(self.audio_var.get(), "normal")

    def _validate(self):
        if not hasattr(self, "run_btn"):
            return
        ok = len(self._clips()) >= 2
        state = "normal" if ok else "disabled"
        if str(self.run_btn.cget("text")) == self._render_label:
            self.run_btn.config(state=state)
        self.preview_btn.config(state=state)
        self._update_summary()

    def _update_summary(self):
        clips = self._clips()
        T = self._duration()
        if len(clips) < 2 or T is None:
            self._status_lbl.config(text="")
            return
        freeze = self.trans_behavior_var.get() == "freeze"
        problem = validate(clips, T, freeze)
        if problem:
            self._status_lbl.config(text="⚠ " + problem, fg=CLR["orange"])
        else:
            total = total_duration(clips, T, freeze)
            note = _tr("crossfader.summary", "{n} clips → {d} total").format(
                n=len(clips), d=ff.format_time(total, ms=False)
            )
            if any(not c.has_audio for c in clips) and any(c.has_audio for c in clips):
                note += "  ·  " + _tr(
                    "crossfader.silence_note", "silence added for clips without audio"
                )
            self._status_lbl.config(text=note, fg=CLR["accent"])

    # ── rendering ────────────────────────────────────────────────────────────
    def _prepare(self, out: str, preview: bool = False):
        clips = self._clips()
        T = self._duration()
        if T is None:
            messagebox.showwarning(
                t("common.warning"),
                _tr("crossfader.bad_duration", "Enter a valid transition length."),
                parent=self,
            )
            return None
        freeze = self.trans_behavior_var.get() == "freeze"
        problem = validate(clips, T, freeze)
        if problem:
            messagebox.showwarning(t("common.warning"), problem, parent=self)
            return None
        container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
        if preview:
            venc = enc.video("h264", "low", speed="fastest", hw="auto", container="mp4")
            w0, h0 = clips[0].width, clips[0].height
            scale = min(1.0, 640 / max(w0, 1), 360 / max(h0, 1))
            size = (_even(w0 * scale), _even(h0 * scale))
        else:
            if not enc.video_ok_for(container, self.encoder.codec):
                messagebox.showwarning(
                    t("common.warning"),
                    _tr(
                        "crossfader.codec_container",
                        "{codec} can't be stored in a .{ext} file. Pick another codec or file type.",
                    ).format(codec=self.encoder.codec.upper(), ext=container),
                    parent=self,
                )
                return None
            venc = self.encoder.video_encode(container)
            size = None
        return build_command(
            clips,
            out,
            T=T,
            transitions=self._transitions(),
            freeze=freeze,
            audio_mode=self._audio_mode(),
            audio_curve=self.audio_curve_var.get(),
            venc=venc,
            size=size,
        )

    def _start_render(self):
        clips = self._clips()
        if len(clips) < 2:
            messagebox.showwarning(
                t("common.warning"),
                _tr("crossfader.need_two", "Add at least two clips."),
                parent=self,
            )
            return
        out = self.out_var.get().strip() or self.suggest_output(clips[0].path, "crossfaded", ".mp4")
        out = self.confirm_output(out, [c.path for c in clips])
        if not out:
            return
        self.out_var.set(out)
        built = self._prepare(out)
        if not built:
            return
        cmd, seconds = built
        self.log(
            self.console,
            f"Rendering {len(clips)} clips → {os.path.basename(out)} "
            f"({ff.format_time(seconds, ms=False)}, {self.encoder.video_encode().label})",
        )
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.run_btn,
            btn_label=self._render_label,
            duration=seconds,
            output=out,
            inputs=[c.path for c in clips],
            name="Crossfader",
        )

    def _start_preview(self):
        from core.paths import temp_dir

        out = os.path.join(temp_dir("crossfade_preview_"), "preview.mp4")
        built = self._prepare(out, preview=True)
        if not built:
            return
        cmd, seconds = built
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: launch_preview(out) if rc == 0 else self.show_result(rc, out),
            btn=self.preview_btn,
            btn_label=self._preview_label,
            duration=seconds,
            output=out,
            name="Crossfader preview",
        )
