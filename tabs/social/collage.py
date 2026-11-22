"""
tabs/social/collage.py  -  Video Collage / Grid

Any number of clips on one canvas, playing together.

* Auto layout (default): the arrangement that shows every clip as large as
  possible while keeping them similar in size - uniform grids, gallery-style
  rows or columns for mixed shapes (see core/grid.py).  Fixed grids and a
  picture-in-picture layout are still there.
* Fit (whole clips, bars where shapes differ) or fill (cells filled, edges
  cropped), gap and background colour.
* Length: the longest clip by default (shorter ones loop), the shortest, or
  any length.  With looping off, a finished clip holds its last frame,
  leaves the background showing, or the others re-arrange to fill its
  space ("reflow").
* Sound from the first clip, all clips mixed, or none.
"""

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import grid
from core.i18n import t
from core.ops.shared import frame_rate
from core.scaling import px
from core.timecode import Timecode
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab
from tabs.kit import dialogs
from tabs.widgets import EncoderPanel


def _fmt(seconds):
    return f"{Timecode.of(seconds):.2}"


LAYOUTS = {  # stable ids; labels are what the user sees
    "auto": {"label": "Auto - best fit for any number of clips", "auto": True, "slots": 1},
    "2x1": {"label": "2×1 (Side by side)", "cols": 2, "rows": 1, "slots": 2},
    "1x2": {"label": "1×2 (Stacked)", "cols": 1, "rows": 2, "slots": 2},
    "2x2": {"label": "2×2 (Quad grid)", "cols": 2, "rows": 2, "slots": 4},
    "3x1": {"label": "3×1 (Triple wide)", "cols": 3, "rows": 1, "slots": 3},
    "1x3": {"label": "1×3 (Triple tall)", "cols": 1, "rows": 3, "slots": 3},
    "3x2": {"label": "3×2 (Six pack)", "cols": 3, "rows": 2, "slots": 6},
    "3x3": {"label": "3×3 (Nine grid)", "cols": 3, "rows": 3, "slots": 9},
    "pip": {
        "label": "Main video + 2 picture-in-picture",
        "cols": 2,
        "rows": 2,
        "slots": 3,
        "custom": "pip",
    },
}
BACKGROUNDS = {"Black": "black", "White": "white", "Grey": "gray", "Charcoal": "0x1E1E1E"}
AUDIO_MODES = {"first": "Audio from clip 1", "mix": "Mix all clips", "none": "No audio"}
LENGTHS = {
    "longest": "As long as the longest clip",
    "shortest": "As long as the shortest clip",
    "custom": "Custom length (seconds):",
}
ENDS = {
    "hold": "hold its last frame",
    "background": "show the background",
    "reflow": "re-arrange the others",
}
MAX_CLIPS = 25


def collage_graph(
    layout: dict,
    n_inputs: int,
    total_w: int,
    total_h: int,
    pad: int,
    bg: str,
    fps: str,
    shortest: bool,
    audio: str,
    has_audio: list,
) -> tuple:
    """(filter_complex, audio_out) with exact, even geometry."""
    total_w, total_h = total_w // 2 * 2, total_h // 2 * 2
    parts = []
    if layout.get("custom") == "pip":
        sw, sh = (total_w // 4) // 2 * 2, (total_h // 4) // 2 * 2
        m = max(pad, total_h // 30)
        parts.append(
            f"[0:v]scale={total_w}:{total_h}:force_original_aspect_ratio=decrease,"
            f"pad={total_w}:{total_h}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps},"
            f"format=yuv420p[base]"
        )
        last = "[base]"
        corners = [(f"W-w-{m}", f"H-h-{m}"), (f"W-w-{m}", f"{m}")]
        for i in range(1, n_inputs):
            parts.append(
                f"[{i}:v]scale={sw}:{sh}:force_original_aspect_ratio=decrease,"
                f"pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps},"
                f"format=yuv420p[p{i}]"
            )
            x, y = corners[(i - 1) % len(corners)]
            parts.append(
                f"{last}[p{i}]overlay=x={x}:y={y}:shortest={1 if shortest else 0}:"
                f"eof_action=repeat[o{i}]"
            )
            last = f"[o{i}]"
        parts.append(f"{last}null[v]")
    else:
        cols, rows = layout["cols"], layout["rows"]
        cell_w = ((total_w - pad * (cols + 1)) // cols) // 2 * 2
        cell_h = ((total_h - pad * (rows + 1)) // rows) // 2 * 2
        spots = []
        for i in range(n_inputs):
            r, c = divmod(i, cols)
            spots.append(f"{pad + c * (cell_w + pad)}_{pad + r * (cell_h + pad)}")
            parts.append(
                f"[{i}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps},"
                f"format=yuv420p[c{i}]"
            )
        cells = "".join(f"[c{i}]" for i in range(n_inputs))
        parts.append(
            f"{cells}xstack=inputs={n_inputs}:layout={'|'.join(spots)}:fill={bg}:"
            f"shortest={1 if shortest else 0}[grid]"
        )
        parts.append(f"[grid]pad={total_w}:{total_h}:0:0:color={bg},setsar=1[v]")
    srcs = [i for i in range(n_inputs) if has_audio[i]]
    if audio == "none" or not srcs:
        return ";".join(parts), False
    if audio == "first" or len(srcs) == 1:
        parts.append(f"[{srcs[0]}:a:0]aresample=48000[a]")
    else:
        parts.append(
            "".join(f"[{i}:a:0]" for i in srcs)
            + f"amix=inputs={len(srcs)}:duration={'shortest' if shortest else 'longest'}:"
            f"normalize=0,aresample=48000[a]"
        )
    return ";".join(parts), True


class VideoCollageTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._slots = []  # list of {path, name, duration, aspect, has_audio}
        self._job = None
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.video_collage"),
            "Any number of clips on one screen, playing together.",
            icon="🎞",
        )
        layout_lf = tk.LabelFrame(
            self,
            text=f"  {t('collage.grid_layout_section')}  ",
            padx=px(15),
            pady=px(8),
            font=(UI_FONT, 9, "bold"),
        )
        layout_lf.pack(fill="x", padx=px(20), pady=px(10, 6))
        left = tk.Frame(layout_lf)
        left.pack(side="left", fill="y")
        self._layout_var = tk.StringVar(value=LAYOUTS["auto"]["label"])
        lb = ttk.Combobox(
            left,
            textvariable=self._layout_var,
            values=[v["label"] for v in LAYOUTS.values()],
            state="readonly",
            width=40,
        )
        lb.pack(anchor="w")
        lb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self._mode_var = tk.StringVar(value="fit")
        tk.Radiobutton(
            left,
            text="Show whole clips (bars where shapes differ)",
            variable=self._mode_var,
            value="fit",
            command=self._changed,
        ).pack(anchor="w", pady=px(6, 0))
        tk.Radiobutton(
            left,
            text="Fill each space (crop the edges)",
            variable=self._mode_var,
            value="fill",
            command=self._changed,
        ).pack(anchor="w")
        self._layout_lbl = tk.Label(
            left, text="", fg=CLR["accent"], justify="left", wraplength=px(330)
        )
        self._layout_lbl.pack(anchor="w", pady=px(6, 0))
        self._grid_canvas = tk.Canvas(
            layout_lf, bg=CLR["console_bg"], width=px(320), height=px(180), highlightthickness=0
        )
        self._grid_canvas.pack(side="right", padx=px(10), pady=px(4))

        slots_lf = tk.LabelFrame(
            self,
            text=f"  {t('collage.video_slots_section')}  ",
            padx=px(15),
            pady=px(6),
            font=(UI_FONT, 9, "bold"),
        )
        slots_lf.pack(fill="both", expand=True, padx=px(20), pady=px(4))
        stb = tk.Frame(slots_lf)
        stb.pack(fill="x", pady=px(0, 4))
        for text, cmd in (
            ("➕ " + t("collage.add_videos_button"), self._add_videos),
            ("🗑 " + t("collage.remove_button"), self._remove_selected),
            ("⬆", self._move_up),
            ("⬇", self._move_down),
            (t("collage.clear_button"), self._clear_all),
        ):
            tk.Button(stb, text=text, relief="flat", cursor="hand2", command=cmd).pack(
                side="left", padx=px(2)
            )
        self._slot_count_lbl = tk.Label(stb, text=t("collage.0_videos_loaded"), fg=CLR["fgdim"])
        self._slot_count_lbl.pack(side="right", padx=px(8))
        list_f = tk.Frame(slots_lf)
        list_f.pack(fill="both", expand=True)
        self._slot_list = tk.Listbox(
            list_f,
            bg=CLR["console_bg"],
            fg=CLR["console_fg"],
            font=(MONO_FONT, 9),
            selectmode="browse",
            height=5,
            relief="flat",
            bd=0,
        )
        lsb = ttk.Scrollbar(list_f, command=self._slot_list.yview)
        self._slot_list.config(yscrollcommand=lsb.set)
        self._slot_list.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        out_lf = tk.LabelFrame(
            self,
            text=f"  {t('collage.output_settings_section')}  ",
            padx=px(15),
            pady=px(6),
            font=(UI_FONT, 9, "bold"),
        )
        out_lf.pack(fill="x", padx=px(20), pady=px(4))
        or1 = tk.Frame(out_lf)
        or1.pack(fill="x", pady=px(2))
        tk.Label(or1, text=t("collage.output_resolution_label")).pack(side="left")
        self._out_w, self._out_h = tk.StringVar(value="1920"), tk.StringVar(value="1080")
        tk.Entry(or1, textvariable=self._out_w, width=6).pack(side="left", padx=px(4))
        tk.Label(or1, text="×").pack(side="left")
        tk.Entry(or1, textvariable=self._out_h, width=6).pack(side="left", padx=px(4))
        for w, h, lbl in [
            (1920, 1080, "1080p"),
            (1280, 720, "720p"),
            (3840, 2160, "4K"),
            (1080, 1920, "9:16"),
            (1080, 1080, "1:1"),
        ]:
            tk.Button(
                or1,
                text=lbl,
                relief="flat",
                width=5,
                command=lambda _w=w, _h=h: (self._out_w.set(str(_w)), self._out_h.set(str(_h))),
            ).pack(side="left", padx=px(2))
        for v in (self._out_w, self._out_h):
            v.trace_add("write", lambda *_a: self._changed())
        or2 = tk.Frame(out_lf)
        or2.pack(fill="x", pady=px(2))
        tk.Label(or2, text=t("collage.grid_padding_label")).pack(side="left")
        self._pad_var = tk.StringVar(value="4")
        tk.Entry(or2, textvariable=self._pad_var, width=4).pack(side="left", padx=px(4))
        self._pad_var.trace_add("write", lambda *_a: self._changed())
        tk.Label(or2, text="px   Background:").pack(side="left")
        self._bg_color = tk.StringVar(value="Black")
        ttk.Combobox(
            or2, textvariable=self._bg_color, width=9, values=list(BACKGROUNDS), state="readonly"
        ).pack(side="left", padx=px(4))
        tk.Label(or2, text="  Sound:").pack(side="left")
        self._audio_mode = tk.StringVar(value=AUDIO_MODES["first"])
        ttk.Combobox(
            or2,
            textvariable=self._audio_mode,
            values=list(AUDIO_MODES.values()),
            state="readonly",
            width=16,
        ).pack(side="left", padx=px(4))
        or3 = tk.Frame(out_lf)
        or3.pack(fill="x", pady=px(2))
        tk.Label(or3, text="Length:").pack(side="left")
        self._length_var = tk.StringVar(value=LENGTHS["longest"])
        lc = ttk.Combobox(
            or3,
            textvariable=self._length_var,
            values=list(LENGTHS.values()),
            state="readonly",
            width=28,
        )
        lc.pack(side="left", padx=px(4))
        lc.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self._custom_len = tk.StringVar(value="30")
        tk.Entry(or3, textvariable=self._custom_len, width=7).pack(side="left")
        or4 = tk.Frame(out_lf)
        or4.pack(fill="x", pady=px(2))
        self._loop_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            or4, text="Loop shorter clips", variable=self._loop_var, command=self._changed
        ).pack(side="left")
        tk.Label(or4, text="   otherwise, when a clip ends:").pack(side="left")
        self._end_var = tk.StringVar(value=ENDS["hold"])
        self._end_cb = ttk.Combobox(
            or4, textvariable=self._end_var, values=list(ENDS.values()), state="readonly", width=24
        )
        self._end_cb.pack(side="left", padx=px(4))
        self._end_cb.bind("<<ComboboxSelected>>", lambda _e: self._changed())
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"), show_ten_bit=False)
        self.encoder.pack(anchor="w", pady=px(4, 0))

        of = tk.Frame(self)
        of.pack(fill="x", padx=px(20), pady=px(4))
        tk.Label(of, text=t("common.output_file"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._out_var = tk.StringVar()
        tk.Entry(of, textvariable=self._out_var, width=55).pack(side="left", padx=px(8))
        tk.Button(of, text=t("common.save_as"), command=self._browse_out, relief="flat").pack(
            side="left"
        )
        self.btn_render = tk.Button(
            self,
            text=t("collage.build_collage"),
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=26,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(6))
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(2, 10))
        self.console, csb = self.make_console(cf, height=4)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self._changed()

    # ── State helpers ─────────────────────────────────────────────────
    def _layout_key(self) -> str:
        return next((k for k, v in LAYOUTS.items() if v["label"] == self._layout_var.get()), "auto")

    def _key(self, table, var, default):
        return next((k for k, v in table.items() if v == var.get()), default)

    def _canvas_size(self) -> tuple:
        try:
            return (
                int(ff_number(self._out_w.get(), 64, 8192)) // 2 * 2,
                int(ff_number(self._out_h.get(), 64, 8192)) // 2 * 2,
            )
        except ValueError:
            return 1920, 1080

    def _gap(self) -> int:
        try:
            return int(ff_number(self._pad_var.get() or "0", 0, 200))
        except ValueError:
            return 0

    def _clips_for_layout(self) -> list:
        key = self._layout_key()
        spec = LAYOUTS[key]
        if spec.get("auto"):
            return self._slots[:MAX_CLIPS]
        return self._slots[: spec["slots"]]

    def _layout(self, aspects: list):
        key = self._layout_key()
        spec = LAYOUTS[key]
        w, h = self._canvas_size()
        mode = self._mode_var.get()
        if spec.get("auto"):
            return grid.best_layout(aspects, w, h, gap=self._gap(), mode=mode)
        return grid.uniform(aspects, w, h, spec["cols"], self._gap(), mode == "fill")

    def _changed(self):
        loop = self._loop_var.get()
        self._end_cb.config(state="disabled" if loop else "readonly")
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(150, self._draw_grid_preview)

    def _draw_grid_preview(self):
        self._job = None
        c = self._grid_canvas
        c.delete("all")
        key = self._layout_key()
        spec = LAYOUTS[key]
        W, H = self._canvas_size()
        cw, ch = px(320), px(180)
        scale = min(cw / W, ch / H)
        ox, oy = (cw - W * scale) / 2, (ch - H * scale) / 2
        c.create_rectangle(ox, oy, ox + W * scale, oy + H * scale, outline="#444444", width=px(1))
        if spec.get("custom") == "pip":
            self._layout_lbl.config(text="Main video full screen, the others in the corners.")
            return
        clips = self._clips_for_layout()
        aspects = [s["aspect"] for s in clips] or [16 / 9] * (
            4 if spec.get("auto") else spec["slots"]
        )
        if not spec.get("auto") and len(aspects) < spec["slots"]:
            aspects += [16 / 9] * (spec["slots"] - len(aspects))
        try:
            lay = self._layout(aspects)
        except ValueError:
            return
        colors = [
            CLR["accent"],
            CLR["green"],
            CLR["orange"],
            CLR.get("pink", "#E91E63"),
            "#9C27B0",
            "#00BCD4",
            "#FF5722",
            "#8BC34A",
            "#FFC107",
        ]
        for i, (x, y, w, h) in enumerate(lay.rects):
            x1, y1 = ox + x * scale, oy + y * scale
            c.create_rectangle(
                x1,
                y1,
                x1 + w * scale,
                y1 + h * scale,
                fill=colors[i % len(colors)],
                outline="#222222",
                width=px(1),
            )
            name = clips[i]["name"][:10] if i < len(clips) else f"#{i + 1}"
            c.create_text(
                x1 + w * scale / 2,
                y1 + h * scale / 2,
                text=name,
                fill="white",
                font=(UI_FONT, 7, "bold"),
            )
        extra = ""
        if spec.get("auto") and len(self._slots) > 16:
            extra = "\nMany clips play at once - rendering will be slow."
        self._layout_lbl.config(text=lay.describe() + extra)

    # ── Clips ─────────────────────────────────────────────────────────
    def load_file(self, path) -> bool:
        if len(self._slots) >= MAX_CLIPS:
            self.log(self.console, f"At most {MAX_CLIPS} clips.")
            return True
        info = ff.try_probe(path)
        if info is None or not info.has_video:
            self.log(self.console, f"{os.path.basename(path)}: no video - skipped.")
            return True
        w, h = info.require_video().display_size
        sar = info.require_video().sar or "1:1"
        try:
            num, den = (int(x) for x in sar.split(":"))
            sar_f = num / den if num and den else 1.0
        except ValueError:
            sar_f = 1.0
        dur = (
            info.require_video().duration if info.require_video().duration > 0.05 else info.duration
        )
        self._slots.append(
            {
                "path": path,
                "name": os.path.basename(path),
                "duration": dur,
                "aspect": (w * sar_f) / max(1, h),
                "has_audio": info.has_audio,
            }
        )
        self._refresh_list()
        return True

    def _add_videos(self):
        for p in dialogs.ask_open_many("video", title="Add clips", parent=self):
            self.load_file(p)

    def _remove_selected(self):
        sel = self._slot_list.curselection()
        if sel:
            self._slots.pop(sel[0])
            self._refresh_list()

    def _move_up(self):
        sel = self._slot_list.curselection()
        if sel and sel[0] > 0:
            i = sel[0]
            self._slots[i - 1], self._slots[i] = self._slots[i], self._slots[i - 1]
            self._refresh_list()
            self._slot_list.selection_set(i - 1)

    def _move_down(self):
        sel = self._slot_list.curselection()
        if sel and sel[0] < len(self._slots) - 1:
            i = sel[0]
            self._slots[i + 1], self._slots[i] = self._slots[i], self._slots[i + 1]
            self._refresh_list()
            self._slot_list.selection_set(i + 1)

    def _clear_all(self):
        self._slots.clear()
        self._refresh_list()

    def _refresh_list(self):
        self._slot_list.delete(0, "end")
        for i, s in enumerate(self._slots):
            self._slot_list.insert("end", f"  #{i + 1}   {s['name']:<40s}  {_fmt(s['duration'])}")
        self._slot_count_lbl.config(text=f"{len(self._slots)} clip(s)")
        self._changed()

    def _browse_out(self):
        p = dialogs.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
        if p:
            self._out_var.set(p)

    # ── Render ────────────────────────────────────────────────────────
    def _render(self):
        from core import encoders as enc
        from tabs import common

        key = self._layout_key()
        spec = LAYOUTS[key]
        clips = self._clips_for_layout()
        need = 2 if spec.get("auto") else spec["slots"]
        if len(clips) < need:
            messagebox.showwarning(
                t("collage.not_enough_title"),
                f"This layout needs {need} clips - {len(self._slots)} added.",
                parent=self,
            )
            return
        W, H = self._canvas_size()
        durs = [s["duration"] for s in clips]
        length_key = self._key(LENGTHS, self._length_var, "longest")
        try:
            total = (
                max(durs)
                if length_key == "longest"
                else min(durs)
                if length_key == "shortest"
                else common.number(self._custom_len.get(), "Length", 0.5, 24 * 3600)
            )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        paths = [s["path"] for s in clips]
        out = self._out_var.get().strip() or self.suggest_output(paths[0], "collage")
        out = self.confirm_output(out, paths)
        if not out:
            return
        self._out_var.set(out)
        loop = self._loop_var.get()
        end = self._key(ENDS, self._end_var, "hold")
        audio = self._key(AUDIO_MODES, self._audio_mode, "first")
        bg = BACKGROUNDS.get(self._bg_color.get(), "black")
        infos = [ff.try_probe(p) for p in paths]
        fps = frame_rate(infos[0]) if infos[0] is not None else "30"
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        cmd = [ff.ffmpeg_path(), "-y", *venc.pre_input]
        if spec.get("custom") == "pip":
            graph, has_a = collage_graph(
                spec,
                len(clips),
                W,
                H,
                self._gap(),
                bg,
                fps,
                length_key == "shortest",
                audio,
                [s["has_audio"] for s in clips],
            )
            for p in paths:
                cmd += ["-i", p]
        else:
            gclips = [
                grid.Clip(i, s["aspect"], s["duration"], s["has_audio"])
                for i, s in enumerate(clips)
            ]
            layouts: dict[grid.LayoutKey, grid.Layout] | None = (
                None if spec.get("auto") else {"all": self._layout([c.aspect for c in gclips])}
            )
            if not spec.get("auto") and not loop and end == "reflow":
                end = "hold"  # fixed grids keep their cells
            canvas = grid.Canvas(
                W, H, fps=fps, gap=self._gap(), mode=self._mode_var.get(), background=bg
            )
            timing = grid.Timing(total, loop=loop, end=end, audio=audio)
            graph, has_a = grid.collage_filter(gclips, canvas, timing, layouts)
            for p in paths:
                cmd += (["-stream_loop", "-1"] if loop else []) + ["-i", p]
        if venc.filter_suffix:
            graph += f";[v]{venc.filter_suffix}[venc]"
        cmd += ["-filter_complex", graph, "-map", "[venc]" if venc.filter_suffix else "[v]"]
        cmd += (
            ["-map", "[a]", *enc.audio(enc.default_audio_for(container), container=container)]
            if has_a
            else ["-an"]
        )
        cmd += [*venc.args, "-t", f"{total:.3f}", out]
        what = (
            spec["label"]
            if not spec.get("auto")
            else self._layout([c["aspect"] for c in clips]).describe()
        )
        self.log(self.console, f"{len(clips)} clips · {what} · {W}×{H} · {_fmt(total)}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            btn_label=t("collage.build_collage"),
            duration=total,
            output=out,
            inputs=paths,
            name="Collage",
        )


def ff_number(text: str, lo: float, hi: float) -> float:
    v = float(str(text).strip())
    if not lo <= v <= hi:
        raise ValueError(f"must be between {lo:g} and {hi:g}")
    return v
