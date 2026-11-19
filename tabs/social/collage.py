"""
tab_videocollage.py  ─  Video Collage / Grid Layout

Combine 2-9 videos into a single grid layout (2×1, 2×2, 3×3, etc.)
playing simultaneously.  Popular for:
  • Multi-cam gameplay / IRL streams
  • Before/after comparisons
  • Reaction compilations
  • "Every angle" moments

Uses FFmpeg's xstack filter for precise grid positioning.
"""

import tkinter as tk
from tkinter import messagebox, ttk
from core import dialogs
import os

from tabs.base_tab import BaseTab, CLR, UI_FONT, MONO_FONT
from core.hardware import (    get_video_duration,
)
from core.i18n import t
from tabs.widgets import EncoderPanel


def _fmt(seconds):
    m, s = divmod(max(0, seconds), 60)
    return f"{int(m):02d}:{s:05.2f}"


LAYOUTS = {   # stable ids; labels are what the user sees
    "2x1": {"label": "2×1 (Side by side)", "cols": 2, "rows": 1, "slots": 2},
    "1x2": {"label": "1×2 (Stacked)", "cols": 1, "rows": 2, "slots": 2},
    "2x2": {"label": "2×2 (Quad grid)", "cols": 2, "rows": 2, "slots": 4},
    "3x1": {"label": "3×1 (Triple wide)", "cols": 3, "rows": 1, "slots": 3},
    "1x3": {"label": "1×3 (Triple tall)", "cols": 1, "rows": 3, "slots": 3},
    "3x2": {"label": "3×2 (Six pack)", "cols": 3, "rows": 2, "slots": 6},
    "3x3": {"label": "3×3 (Nine grid)", "cols": 3, "rows": 3, "slots": 9},
    "pip": {"label": "Main video + 2 picture-in-picture", "cols": 2, "rows": 2, "slots": 3, "custom": "pip"},
}
BACKGROUNDS = {"Black": "black", "White": "white", "Grey": "gray", "Charcoal": "0x1E1E1E"}
AUDIO_MODES = {"first": "Audio from clip 1", "mix": "Mix all clips", "none": "No audio"}


def collage_graph(layout: dict, n_inputs: int, total_w: int, total_h: int, pad: int, bg: str,
                  fps: str, shortest: bool, audio: str, has_audio: list) -> tuple:
    """(filter_complex, audio_out) with exact, even geometry."""
    total_w, total_h = total_w // 2 * 2, total_h // 2 * 2
    parts = []
    if layout.get("custom") == "pip":
        sw, sh = (total_w // 4) // 2 * 2, (total_h // 4) // 2 * 2
        m = max(pad, total_h // 30)
        parts.append(f"[0:v]scale={total_w}:{total_h}:force_original_aspect_ratio=decrease,"
                     f"pad={total_w}:{total_h}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps},"
                     f"format=yuv420p[base]")
        last = "[base]"
        corners = [(f"W-w-{m}", f"H-h-{m}"), (f"W-w-{m}", f"{m}")]
        for i in range(1, n_inputs):
            parts.append(f"[{i}:v]scale={sw}:{sh}:force_original_aspect_ratio=decrease,"
                         f"pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps},"
                         f"format=yuv420p[p{i}]")
            x, y = corners[(i - 1) % len(corners)]
            parts.append(f"{last}[p{i}]overlay=x={x}:y={y}:shortest={1 if shortest else 0}:"
                         f"eof_action=repeat[o{i}]")
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
            parts.append(f"[{i}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                         f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps},"
                         f"format=yuv420p[c{i}]")
        cells = "".join(f"[c{i}]" for i in range(n_inputs))
        parts.append(f"{cells}xstack=inputs={n_inputs}:layout={'|'.join(spots)}:fill={bg}:"
                     f"shortest={1 if shortest else 0}[grid]")
        parts.append(f"[grid]pad={total_w}:{total_h}:0:0:color={bg},setsar=1[v]")
    srcs = [i for i in range(n_inputs) if has_audio[i]]
    if audio == "none" or not srcs:
        return ";".join(parts), False
    if audio == "first" or len(srcs) == 1:
        parts.append(f"[{srcs[0]}:a:0]aresample=48000[a]")
    else:
        parts.append("".join(f"[{i}:a:0]" for i in srcs) +
                     f"amix=inputs={len(srcs)}:duration={'shortest' if shortest else 'longest'}:"
                     f"normalize=0,aresample=48000[a]")
    return ";".join(parts), True


class VideoCollageTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._slots = []  # list of {path, name, duration}
        self._build_ui()

    def _build_ui(self):
        self.make_header(self, t("tab.video_collage"),
                         t("collage.subtitle"),
                         icon="🎞")

        # ── Layout picker ─────────────────────────────────────────────────
        layout_lf = tk.LabelFrame(self, text=f"  {t('collage.grid_layout_section')}  ", padx=15, pady=10,
                                  font=(UI_FONT, 9, "bold"))
        layout_lf.pack(fill="x", padx=20, pady=(10, 6))

        self._layout_var = tk.StringVar(value="2x2")
        lr = tk.Frame(layout_lf)
        lr.pack(fill="x")
        for key, spec in LAYOUTS.items():
            tk.Radiobutton(lr, text=spec["label"], variable=self._layout_var,
                           value=key, font=(UI_FONT, 10),
                           command=self._on_layout_change).pack(anchor="w", pady=1)

        # ── Grid preview canvas ───────────────────────────────────────────
        self._grid_canvas = tk.Canvas(layout_lf, bg=CLR["console_bg"],
                                      width=280, height=160, highlightthickness=0)
        self._grid_canvas.pack(side="right", padx=10, pady=4)

        # ── Video slots ───────────────────────────────────────────────────
        slots_lf = tk.LabelFrame(self, text=f"  {t('collage.video_slots_section')}  ", padx=15, pady=8,
                                 font=(UI_FONT, 9, "bold"))
        slots_lf.pack(fill="both", expand=True, padx=20, pady=6)

        # Toolbar
        stb = tk.Frame(slots_lf, bg=CLR["panel"])
        stb.pack(fill="x", pady=(0, 6))
        tk.Button(stb, text="➕ " + t("collage.add_videos_button"), bg=CLR["accent"], fg="white",
                  font=(UI_FONT, 10, "bold"), cursor="hand2", relief="flat",
                  command=self._add_videos).pack(side="left", padx=4)
        tk.Button(stb, text="🗑 " + t("collage.remove_button"), bg=CLR["red"], fg="white",
                  font=(UI_FONT, 9), cursor="hand2", relief="flat",
                  command=self._remove_selected).pack(side="left", padx=4)
        tk.Button(stb, text="⬆", bg=CLR["panel"], fg=CLR["fg"],
                  font=(UI_FONT, 11, "bold"), cursor="hand2", relief="flat", width=3,
                  command=self._move_up).pack(side="left", padx=2)
        tk.Button(stb, text="⬇", bg=CLR["panel"], fg=CLR["fg"],
                  font=(UI_FONT, 11, "bold"), cursor="hand2", relief="flat", width=3,
                  command=self._move_down).pack(side="left", padx=2)
        tk.Button(stb, text="🗑 " + t("collage.clear_button"), bg=CLR["panel"], fg=CLR["fg"],
                  font=(UI_FONT, 9), cursor="hand2", relief="flat",
                  command=self._clear_all).pack(side="left", padx=8)

        self._slot_count_lbl = tk.Label(stb, text=t("collage.0_videos_loaded"),
                                        bg=CLR["panel"], fg=CLR["fgdim"],
                                        font=(UI_FONT, 10))
        self._slot_count_lbl.pack(side="right", padx=8)

        # Listbox
        list_f = tk.Frame(slots_lf)
        list_f.pack(fill="both", expand=True)
        self._slot_list = tk.Listbox(
            list_f, bg=CLR["console_bg"], fg=CLR["console_fg"],
            font=(MONO_FONT, 9), selectmode="browse", height=5,
            relief="flat", bd=0)
        lsb = ttk.Scrollbar(list_f, command=self._slot_list.yview)
        self._slot_list.config(yscrollcommand=lsb.set)
        self._slot_list.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        # ── Output settings ───────────────────────────────────────────────
        out_lf = tk.LabelFrame(self, text=f"  {t('collage.output_settings_section')}  ", padx=15, pady=8,
                               font=(UI_FONT, 9, "bold"))
        out_lf.pack(fill="x", padx=20, pady=6)

        or1 = tk.Frame(out_lf)
        or1.pack(fill="x", pady=3)
        tk.Label(or1, text=t("collage.output_resolution_label"), font=(UI_FONT, 10)).pack(side="left")
        self._out_w = tk.StringVar(value="1920")
        tk.Entry(or1, textvariable=self._out_w, width=6, relief="flat",
                 font=(UI_FONT, 10)).pack(side="left", padx=4)
        tk.Label(or1, text="×", font=(UI_FONT, 10)).pack(side="left")
        self._out_h = tk.StringVar(value="1080")
        tk.Entry(or1, textvariable=self._out_h, width=6, relief="flat",
                 font=(UI_FONT, 10)).pack(side="left", padx=4)

        # Quick presets
        for w, h, lbl in [(1920, 1080, "1080p"), (1280, 720, "720p"),
                           (3840, 2160, "4K"), (1080, 1920, "9:16")]:
            tk.Button(or1, text=lbl, bg=CLR["panel"], fg=CLR["fg"],
                      font=(UI_FONT, 9), cursor="hand2", width=5,
                      command=lambda _w=w, _h=h: (self._out_w.set(str(_w)),
                                                   self._out_h.set(str(_h)))
                      ).pack(side="left", padx=2)

        or2 = tk.Frame(out_lf)
        or2.pack(fill="x", pady=3)
        tk.Label(or2, text="Audio:", font=(UI_FONT, 10)).pack(side="left")
        self.encoder = EncoderPanel(out_lf, codecs=("h264", "hevc", "av1"), show_ten_bit=False)
        self.encoder.pack(anchor="w", before=or2)
        self._audio_mode = tk.StringVar(value=AUDIO_MODES["first"])
        ttk.Combobox(or2, textvariable=self._audio_mode, values=list(AUDIO_MODES.values()),
                     state="readonly", width=18).pack(side="left", padx=6)

        tk.Label(or2, text=t("collage.grid_padding_label"), font=(UI_FONT, 10)).pack(side="left", padx=(16, 0))
        self._pad_var = tk.StringVar(value="4")
        tk.Entry(or2, textvariable=self._pad_var, width=4, relief="flat",
                 font=(UI_FONT, 10)).pack(side="left", padx=4)
        tk.Label(or2, text=t("collage.px_label"), fg=CLR["fgdim"], font=(UI_FONT, 9)).pack(side="left")

        tk.Label(or2, text="  " + t("collage.bg_color_label"), font=(UI_FONT, 10)).pack(side="left", padx=(12, 0))
        self._bg_color = tk.StringVar(value="Black")
        ttk.Combobox(or2, textvariable=self._bg_color, width=9, values=list(BACKGROUNDS),
                     state="readonly").pack(side="left", padx=4)

        self._duration_mode = tk.StringVar(value="shortest")
        or3 = tk.Frame(out_lf)
        or3.pack(fill="x", pady=3)
        tk.Label(or3, text=t("collage.duration_section"), font=(UI_FONT, 10)).pack(side="left")
        tk.Radiobutton(or3, text=t("collage.shortest_option"), variable=self._duration_mode,
                       value="shortest", font=(UI_FONT, 10)).pack(side="left", padx=8)
        tk.Radiobutton(or3, text=t("collage.longest_option"),
                       variable=self._duration_mode, value="longest",
                       font=(UI_FONT, 10)).pack(side="left", padx=8)

        # ── Output file ───────────────────────────────────────────────────
        of = tk.Frame(self)
        of.pack(fill="x", padx=20, pady=6)
        tk.Label(of, text=t("common.output_file"), font=(UI_FONT, 10, "bold")).pack(side="left")
        self._out_var = tk.StringVar()
        tk.Entry(of, textvariable=self._out_var, width=55, relief="flat",
                 font=(UI_FONT, 10)).pack(side="left", padx=8)
        tk.Button(of, text=t("common.save_as"), command=self._browse_out, cursor="hand2",
                  relief="flat", font=(UI_FONT, 9)).pack(side="left")

        # ── Run ───────────────────────────────────────────────────────────
        bf = tk.Frame(self)
        bf.pack(pady=8)
        self._btn_run = tk.Button(
            bf, text=t("collage.build_collage"), font=(UI_FONT, 12, "bold"),
            bg=CLR["green"], fg="white", height=2, width=26,
            cursor="hand2", command=self._render)
        self._btn_run.pack()

        # ── Console ───────────────────────────────────────────────────────
        cf = tk.Frame(self)
        cf.pack(fill="both", expand=True, padx=20, pady=(4, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

        self._on_layout_change()

    # ── Layout change → redraw grid preview ───────────────────────────
    def _on_layout_change(self):
        self._draw_grid_preview()

    def _draw_grid_preview(self):
        c = self._grid_canvas
        c.delete("all")
        cw, ch = 280, 160

        layout_name = self._layout_var.get()
        info = LAYOUTS.get(layout_name, LAYOUTS["2x2"])
        cols, rows = info["cols"], info["rows"]
        pad = 4
        cell_w = (cw - pad * (cols + 1)) // cols
        cell_h = (ch - pad * (rows + 1)) // rows

        colors = [CLR["accent"], CLR["green"], CLR["orange"], CLR["pink"],
                  "#9C27B0", "#00BCD4", "#FF5722", "#8BC34A", "#FFEB3B"]

        slot = 0
        for r in range(rows):
            for col in range(cols):
                if slot >= info["slots"]:
                    break
                x1 = pad + col * (cell_w + pad)
                y1 = pad + r * (cell_h + pad)
                x2 = x1 + cell_w
                y2 = y1 + cell_h
                color = colors[slot % len(colors)]
                c.create_rectangle(x1, y1, x2, y2, fill=color, outline="#333333")
                # Label
                label = f"#{slot + 1}"
                if slot < len(self._slots):
                    label = self._slots[slot]["name"][:8]
                c.create_text((x1 + x2) // 2, (y1 + y2) // 2, text=label,
                              fill="white", font=(UI_FONT, 8, "bold"))
                slot += 1

    # ── Slot management ───────────────────────────────────────────────
    def _add_videos(self):
        paths = dialogs.askopenfilenames(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm *.flv *.m4v"),
                       ("All", t("ducker.item_2"))])
        for p in paths:
            dur = get_video_duration(p)
            self._slots.append({
                "path": p,
                "name": os.path.basename(p),
                "duration": dur,
            })
        self._refresh_list()

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
            self._slot_list.insert("end",
                f"  #{i + 1}   {s['name']:<40s}  {_fmt(s['duration'])}")
        self._slot_count_lbl.config(text=f"{len(self._slots)} videos loaded")
        self._draw_grid_preview()

    def _browse_out(self):
        p = dialogs.asksaveasfilename(defaultextension=".mp4",
                                         filetypes=[("MP4", "*.mp4")])
        if p:
            self._out_var.set(p)

    # ── Render ────────────────────────────────────────────────────────
    def _render(self):
        from core import ffmpeg as ff
        from core import encoders as enc
        from tabs import common
        info = LAYOUTS.get(self._layout_var.get(), LAYOUTS["2x2"])
        needed = info["slots"]
        if len(self._slots) < needed:
            messagebox.showwarning(t("collage.not_enough_title"),
                                   f"This layout needs {needed} videos. You have {len(self._slots)}.",
                                   parent=self)
            return
        paths = [self._slots[i]["path"] for i in range(needed)]
        infos = []
        for i, path in enumerate(paths):
            pi = common.check_source(self, path, what=f"Clip {i + 1}")
            if pi is None:
                return
            infos.append(pi)
        try:
            total_w = int(common.number(self._out_w.get(), "Width", 64, 8192))
            total_h = int(common.number(self._out_h.get(), "Height", 64, 8192))
            pad = int(common.number(self._pad_var.get() or "0", "Padding", 0, 200))
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        out = self._out_var.get().strip() or self.suggest_output(paths[0], "collage")
        out = self.confirm_output(out, paths)
        if not out:
            return
        self._out_var.set(out)
        shortest = self._duration_mode.get() == "shortest"
        audio = next((k for k, v in AUDIO_MODES.items() if v == self._audio_mode.get()), "first")
        fps = common.frame_rate(infos[0])
        graph, has_a = collage_graph(info, needed, total_w, total_h, pad,
                                     BACKGROUNDS.get(self._bg_color.get(), "black"), fps, shortest, audio,
                                     [pi.has_audio for pi in infos])
        container = common.container_of(out)
        venc = self.encoder.video_encode(container)
        if venc.filter_suffix:
            graph += f";[v]{venc.filter_suffix}[venc]"
        cmd = [ff.ffmpeg_path(), "-y", *venc.pre_input]
        for path in paths:
            cmd += ["-i", path]
        durs = [pi.duration for pi in infos]
        total = min(durs) if shortest else max(durs)
        cmd += ["-filter_complex", graph, "-map", "[venc]" if venc.filter_suffix else "[v]"]
        cmd += (["-map", "[a]", *enc.audio(enc.default_audio_for(container), container=container)]
                if has_a else ["-an"])
        cmd += [*venc.args, "-t", f"{total:.3f}", out]
        self.log(self.console, f"Building a {info['label']} collage at {total_w // 2 * 2}×{total_h // 2 * 2}…")
        self.run_ffmpeg(cmd, self.console, on_done=lambda rc: self.show_result(rc, out),
                        btn=self._btn_run, btn_label=t("collage.build_collage"), duration=total,
                        output=out, inputs=paths, name="Collage")
