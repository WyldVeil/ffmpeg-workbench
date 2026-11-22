"""
tabs/cutting/sequencer.py  -  Multi-Clip Sequencer

Line up clips, trim each one (type times or set them from the preview), and
join them into one video.  Clips of any size, frame rate or codec are
normalised in a single pass (core/concat.py); identical clips can also be
joined losslessly, with trims moving to keyframes.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import concat
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs import common, framestep
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import EncoderPanel, FramePreview, SaveRow, scroll_body

SIZES = {
    "first": "Same as the first clip",
    "largest": "Largest clip",
    "1920x1080": "1920×1080",
    "1280x720": "1280×720",
    "1080x1920": "1080×1920 (vertical)",
}


class SequencerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.clips: list = []  # dicts: path, duration, start, end
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.multi_clip_sequencer"),
            "Trim and join clips in order - any mix of sizes and formats.",
            icon="🎞",
        )
        body = scroll_body(self)
        top = tk.Frame(body)
        top.pack(fill="x", padx=px(20), pady=px(10, 4))
        left = tk.Frame(top)
        left.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(
            left,
            columns=("n", "file", "in", "out", "len"),
            show="headings",
            height=9,
            selectmode="browse",
        )
        for c, label, w in (
            ("n", "#", 36),
            ("file", "Clip", 230),
            ("in", "In", 90),
            ("out", "Out", 90),
            ("len", "Length", 70),
        ):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=px(w), anchor="w", stretch=c == "file")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._selected())
        bt = tk.Frame(left)
        bt.pack(fill="x", pady=px(4))
        for text, cmd in (
            ("➕ Add", self._add),
            ("⬆", lambda: self._move(-1)),
            ("⬇", lambda: self._move(1)),
            ("🗑 Remove", self._remove),
            ("🧹 Clear", self._clear),
        ):
            tk.Button(bt, text=text, relief="flat", command=cmd).pack(side="left", padx=px(2))
        self.total_lbl = tk.Label(bt, text="", fg=CLR["accent"])
        self.total_lbl.pack(side="right")

        right = tk.Frame(top)
        right.pack(side="left", padx=px(10, 0))
        self.preview = FramePreview(right, 384, 216)
        self.preview.pack()
        self.pos_var = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(
            right,
            from_=0,
            to=1,
            variable=self.pos_var,
            command=lambda _v: self.preview.show(float(self.pos_var.get())),
        )
        self.slider.pack(fill="x")
        framestep.slider(self.slider, self.preview)  # , and . step a frame
        trim = tk.Frame(right)
        trim.pack(fill="x", pady=px(2))
        tk.Label(trim, text="In").pack(side="left")
        self.in_var = tk.StringVar()
        tk.Entry(trim, textvariable=self.in_var, width=11).pack(side="left", padx=px(2))
        tk.Button(trim, text="⇤", relief="flat", command=lambda: self._mark("in")).pack(side="left")
        tk.Label(trim, text="Out").pack(side="left", padx=px(8, 0))
        self.out_var = tk.StringVar()
        tk.Entry(trim, textvariable=self.out_var, width=11).pack(side="left", padx=px(2))
        tk.Button(trim, text="⇥", relief="flat", command=lambda: self._mark("out")).pack(
            side="left"
        )
        tk.Button(trim, text="Apply", relief="flat", command=self._apply_trim).pack(
            side="left", padx=px(6)
        )

        opt = tk.LabelFrame(body, text="  Join  ", padx=px(12), pady=px(6))
        opt.pack(fill="x", padx=px(20), pady=px(4))
        r = tk.Frame(opt)
        r.pack(fill="x")
        tk.Label(r, text="Size:").pack(side="left")
        self.size_var = tk.StringVar(value=SIZES["first"])
        ttk.Combobox(
            r, textvariable=self.size_var, values=list(SIZES.values()), state="readonly", width=24
        ).pack(side="left", padx=px(4))
        self.lossless_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r,
            text="Lossless (identical clips only - trims move to keyframes)",
            variable=self.lossless_var,
        ).pack(side="left", padx=px(10))
        self.encoder = EncoderPanel(opt, codecs=("h264", "hevc", "av1", "vp9"))
        self.encoder.pack(anchor="w", pady=px(4))
        self.out_row = SaveRow(opt, t("common.output_file"), suggest=self._suggest)
        self.out_row.pack(fill="x")
        self.btn_render = tk.Button(
            body,
            text="🎞  JOIN",
            font=(UI_FONT, 12, "bold"),
            bg=CLR["green"],
            fg="white",
            width=22,
            cursor="hand2",
            command=self._render,
        )
        self.btn_render.pack(pady=px(8))
        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── list ─────────────────────────────────────────────────────────────────

    def add_paths(self, paths):
        added = 0
        for p in paths:
            info = ff.try_probe(p)
            if info is not None and info.has_video and info.duration > 0:
                self.clips.append(
                    {"path": p, "duration": info.duration, "start": 0.0, "end": info.duration}
                )
                added += 1
        self._fill(len(self.clips) - 1 if added else None)
        return added

    def load_file(self, path):
        return self.add_paths([path]) > 0

    def _add(self):
        self.add_paths(self.pick_sources("video"))

    def _fill(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.clips):
            self.tree.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    i + 1,
                    os.path.basename(c["path"]),
                    ff.format_time(c["start"]),
                    ff.format_time(c["end"]),
                    f"{c['end'] - c['start']:.2f}s",
                ),
            )
        if select is not None and 0 <= select < len(self.clips):
            self.tree.selection_set(str(select))
        total = sum(c["end"] - c["start"] for c in self.clips)
        self.total_lbl.config(text=f"{len(self.clips)} clips · {ff.format_time(total)}")

    def _index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _move(self, d):
        i = self._index()
        if i is not None and 0 <= i + d < len(self.clips):
            self.clips[i], self.clips[i + d] = self.clips[i + d], self.clips[i]
            self._fill(i + d)

    def _remove(self):
        i = self._index()
        if i is not None:
            del self.clips[i]
            self._fill(min(i, len(self.clips) - 1))

    def _clear(self):
        self.clips.clear()
        self._fill()

    def _selected(self):
        i = self._index()
        if i is None:
            return
        c = self.clips[i]
        self.slider.configure(to=max(c["duration"], 0.01))
        self.pos_var.set(c["start"])
        self.preview.load(c["path"], c["duration"], c["start"])
        self.in_var.set(ff.format_time(c["start"]))
        self.out_var.set(ff.format_time(c["end"]))

    def mark_in(self):
        self._mark("in")

    def mark_out(self):
        self._mark("out")

    def _mark(self, which):
        (self.in_var if which == "in" else self.out_var).set(
            ff.format_time(float(self.pos_var.get()))
        )

    def _apply_trim(self):
        i = self._index()
        if i is None:
            return
        c = self.clips[i]
        try:
            a = ff.parse_time(self.in_var.get() or "0")
            b = ff.parse_time(self.out_var.get()) if self.out_var.get().strip() else c["duration"]
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        b = min(b, c["duration"])
        if not 0 <= a < b:
            messagebox.showwarning(
                t("common.warning"), "'In' must be before 'Out', inside the clip.", parent=self
            )
            return
        c["start"], c["end"] = a, b
        self._fill(i)

    def _suggest(self):
        if self.clips:
            return self.suggest_output(self.clips[0]["path"], "sequence")
        return ""

    # ── render ───────────────────────────────────────────────────────────────

    def _render(self):
        if len(self.clips) < 1:
            messagebox.showwarning(t("common.warning"), "Add at least one clip.", parent=self)
            return
        missing = [c["path"] for c in self.clips if not os.path.isfile(c["path"])]
        if missing:
            messagebox.showwarning(t("common.warning"), f"Missing clip:\n{missing[0]}", parent=self)
            return
        out = self.confirm_output(
            self.out_row.path or self._suggest(), [c["path"] for c in self.clips]
        )
        if not out:
            return
        self.out_row.set(out)
        pieces = [concat.Piece.of(c["path"], c["start"], c["end"]) for c in self.clips]
        total = sum(p.length for p in pieces)
        if self.lossless_var.get():
            problems = concat.copy_compatibility([p.info for p in pieces])
            if problems:
                messagebox.showwarning(
                    t("common.warning"),
                    "These clips can't be joined losslessly:\n\n"
                    + "\n".join(problems[:5])
                    + "\n\nUntick 'Lossless' to join them "
                    "with re-encoding.",
                    parent=self,
                )
                return
            from core.paths import temp_dir

            lst = os.path.join(temp_dir("sequence_"), "list.txt")
            cmd = concat.copy_join_command(
                [c["path"] for c in self.clips],
                out,
                lst,
                inpoints=[c["start"] or None for c in self.clips],
                outpoints=[
                    c["end"] if c["end"] < c["duration"] - 0.01 else None for c in self.clips
                ],
            )
            self.log(self.console, "Joining losslessly (trims snap to keyframes)…")
        else:
            key = next((k for k, v in SIZES.items() if v == self.size_var.get()), "first")
            if "x" in key:
                w, h = (int(v) for v in key.split("x"))
                fps = concat.target_geometry(pieces, "first")[2]
            else:
                w, h, fps = concat.target_geometry(pieces, key)
            venc = self.encoder.video_encode(common.container_of(out))
            from core import encoders as enc

            cmd, total = concat.filter_join_command(
                pieces,
                out,
                size=(w, h),
                fps=fps,
                venc=venc,
                audio_codec=enc.default_audio_for(common.container_of(out)),
            )
            self.log(self.console, f"Joining {len(pieces)} clips at {w}×{h} @ {fps}…")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=total,
            output=out,
            inputs=[c["path"] for c in self.clips],
            name="Sequencer",
        )
