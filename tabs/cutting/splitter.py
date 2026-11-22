"""
tab_multisplitter.py  ─  Manual Multi-Splitter
Extract multiple different segments from a single video into separate files.
"""

import os
import re
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox

from core import ffmpeg as ff
from core import outputs
from core.hardware import get_video_duration
from core.i18n import t
from core.ops.splitter import clip_command
from core.queue_manager import Hooks, TaskContext
from core.scaling import px
from core.timecode import Timecode
from tabs import framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, VideoTimeline, add_tooltip
from tabs.kit import dialogs
from tabs.kit.contracts import Transport


def _tr(key, fallback):
    text = t(key)
    return fallback if text == key else text


def _ts(seconds):
    """HH:MM:SS.mmm - exact enough for any frame."""
    return f"{Timecode.of(seconds):H.3}"


def _fmt(seconds):
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{int(h):02d}:{int(m):02d}:{s:05.2f}"
    return f"{int(m):02d}:{s:05.2f}"


def _copy_start(path: str, at: float) -> float:
    """Where a copied clip starts: the keyframe at or before *at*."""
    from core.ops.trim import keyframe_at_or_before

    k = keyframe_at_or_before(path, at)
    return at if k is None else k


class MultiSplitterTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_path = ""
        self.duration = 0.0
        self.split_rows = []
        self._build_ui()

    # ── Contracts (tabs/kit/contracts.py): the playhead is the timeline's ──
    def transport(self) -> Transport | None:
        return self._timeline

    def mark_in(self) -> None:
        self._timeline.mark_in()

    def mark_out(self) -> None:
        self._timeline.mark_out()

    def _build_ui(self):
        self.make_header(self, t("tab.manual_multi_splitter"), t("splitter.subtitle"), icon="✂️")

        # Source File
        sf = tk.Frame(self, bg=CLR["bg"])
        sf.pack(fill="x", padx=px(20), pady=px(10))
        tk.Label(
            sf, text=t("common.source_video"), bg=CLR["bg"], fg=CLR["fg"], font=(UI_FONT, 10)
        ).pack(side="left")
        self.src_var = tk.StringVar()
        tk.Entry(
            sf,
            textvariable=self.src_var,
            width=50,
            bg=CLR["input_bg"],
            fg=CLR["input_fg"],
            font=(UI_FONT, 10),
        ).pack(side="left", padx=px(10))
        tk.Button(
            sf, text=t("btn.browse"), command=self._browse_file, bg=CLR["panel"], fg=CLR["fg"]
        ).pack(side="left")

        # Timeline
        tl_frame = tk.LabelFrame(
            self,
            text=t("splitter.timeline_section")
            if t("splitter.timeline_section") != "splitter.timeline_section"
            else "Timeline",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            padx=px(10),
            pady=px(6),
        )
        tl_frame.pack(fill="x", padx=px(20), pady=px(6, 2))
        self._timeline = VideoTimeline(
            tl_frame, on_change=self._on_timeline_change, height=90, show_handles=True
        )
        self._timeline.pack(fill="x", padx=px(4), pady=px(4))
        self._tl_info = tk.Label(
            tl_frame, text="No file loaded", bg=CLR["bg"], fg=CLR["fgdim"], font=(MONO_FONT, 9)
        )
        self._tl_info.pack(anchor="w", padx=px(4))
        tk.Label(
            tl_frame,
            text="Drag the green / red handles to a clip's start and end - or click a handle "
            "and press , or . to move it one frame - then Add Split Range.",
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(anchor="w", padx=px(4))

        # Options
        opt_f = tk.Frame(self, bg=CLR["bg"])
        opt_f.pack(fill="x", padx=px(20), pady=px(5))
        self.copy_mode = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt_f,
            text=t("splitter.stream_copy_checkbox"),
            variable=self.copy_mode,
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectcolor=CLR["panel"],
        ).pack(side="left")

        # Splits Container
        self.splits_container = tk.LabelFrame(
            self,
            text=t("splitter.split_regions_section"),
            bg=CLR["bg"],
            fg=CLR["fgdim"],
            padx=px(10),
            pady=px(10),
        )
        self.splits_container.pack(fill="both", expand=True, padx=px(20), pady=px(10))

        btn_f = tk.Frame(self.splits_container, bg=CLR["bg"])
        btn_f.pack(fill="x", pady=px(0, 10))
        tk.Button(
            btn_f,
            text=t("splitter.add_split_button"),
            command=self._add_row,
            bg=CLR["panel"],
            fg=CLR["fg"],
        ).pack(side="left")

        self.rows_frame = tk.Frame(self.splits_container, bg=CLR["bg"])
        self.rows_frame.pack(fill="both", expand=True)

        # Add first row by default
        self._add_row()

        # Render Button & Console
        self.btn_render = tk.Button(
            self,
            text=t("splitter.batch_extract_button"),
            font=(UI_FONT, 11, "bold"),
            bg=CLR["green"],
            fg="white",
            command=self._render_all,
        )
        self.btn_render.pack(pady=px(10))
        cf = tk.Frame(self, bg=CLR["bg"])
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(4))
        self.console, csb = self.make_console(cf)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    def _browse_file(self):
        p = dialogs.askopenfilename(
            filetypes=[(t("silence.video_files"), "*.mp4 *.mov *.mkv *.avi *.webm")]
        )
        if p:
            self.load_file(p)

    def on_source_loaded(self, p):
        """Browse, drag and drop and File > Open all come here."""
        self.file_path = p
        self.src_var.set(p)
        self.duration = get_video_duration(p)
        self._timeline.set_duration(self.duration, framestep.fps_of(p))
        self._timeline.set_range(0, self.duration)
        self._tl_info.config(text=f"{os.path.basename(p)}  ·  {_fmt(self.duration)} total")
        # A row nobody has edited yet covers the whole new video
        for r in self.split_rows:
            if (r["start"].get(), r["end"].get()) == ("00:00:00", "00:00:10"):
                r["start"].set(_ts(0))
                r["end"].set(_ts(self.duration))

    def _on_timeline_change(self, start, end, playhead):
        self._tl_info.config(
            text=f"Start {_fmt(start)}  ·  End {_fmt(end)}  ·  Playhead {_fmt(playhead)}"
        )

    def _add_row(self):
        if len(self.split_rows) >= 10:
            messagebox.showwarning(
                t("splitter.limit_reached_title"), t("splitter.limit_reached_message")
            )
            return

        idx = len(self.split_rows) + 1
        start, end = self._timeline.get_range() if self.duration > 0 else (0.0, 10.0)
        row = tk.Frame(self.rows_frame, bg=CLR["bg"])
        row.pack(fill="x", pady=px(4))

        num = tk.Label(row, text=f"Clip {idx}:", bg=CLR["bg"], fg=CLR["accent"], width=6)
        num.pack(side="left")

        tk.Label(row, text=t("splitter.start_label"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left", padx=px(10, 2)
        )
        start_var = tk.StringVar(value=_ts(start) if self.duration > 0 else "00:00:00")
        tk.Entry(
            row, textvariable=start_var, width=13, bg=CLR["input_bg"], fg=CLR["input_fg"]
        ).pack(side="left")

        tk.Label(row, text=t("splitter.end_label"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left", padx=px(10, 2)
        )
        end_var = tk.StringVar(value=_ts(end) if self.duration > 0 else "00:00:10")
        tk.Entry(row, textvariable=end_var, width=13, bg=CLR["input_bg"], fg=CLR["input_fg"]).pack(
            side="left"
        )

        tk.Label(row, text=t("splitter.suffix_label"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            side="left", padx=px(10, 2)
        )
        suffix_var = tk.StringVar(value=f"_clip{idx}")
        tk.Entry(
            row, textvariable=suffix_var, width=15, bg=CLR["input_bg"], fg=CLR["input_fg"]
        ).pack(side="left")

        use = tk.Button(
            row,
            text="⤓ From timeline",
            bg=CLR["panel"],
            fg=CLR["fg"],
            relief="flat",
            command=lambda a=start_var, b=end_var: self._from_timeline(a, b),
        )
        use.pack(side="left", padx=px(8, 0))
        add_tooltip(use, "Set this clip to the range selected on the timeline")

        btn_rm = tk.Button(
            row, text="❌", bg=CLR["bg"], fg=CLR["red"], command=lambda r=row: self._remove_row(r)
        )
        btn_rm.pack(side="left", padx=px(10))

        self.split_rows.append(
            {"frame": row, "start": start_var, "end": end_var, "suffix": suffix_var, "label": num}
        )

    def _from_timeline(self, start_var, end_var):
        if self.duration <= 0:
            return
        a, b = self._timeline.get_range()
        start_var.set(_ts(a))
        end_var.set(_ts(b))

    def _remove_row(self, row_frame):
        row_frame.destroy()
        self.split_rows = [r for r in self.split_rows if r["frame"] != row_frame]
        # Number the clips 1, 2, 3... again (and their suffixes, unless edited)
        for i, r in enumerate(self.split_rows, 1):
            r["label"].config(text=f"Clip {i}:")
            if re.fullmatch(r"_clip\d+", r["suffix"].get()):
                r["suffix"].set(f"_clip{i}")

    def _warn(self, key: str, fallback: str, **values: object) -> None:
        messagebox.showwarning(
            t("common.warning"), _tr(key, fallback).format(**values), parent=self
        )

    def _clips(self, total: float, base: str, ext: str) -> list[tuple[float, float, str]] | None:
        """(start, end, output) of every row; None (after saying why) when one is wrong."""
        jobs: list[tuple[float, float, str]] = []
        seen: set[str] = set()
        for i, data in enumerate(self.split_rows, start=1):
            try:
                ss, ee = ff.parse_time(data["start"].get()), ff.parse_time(data["end"].get())
            except ValueError:
                self._warn(
                    "splitter.bad_time",
                    "Clip {n}: times must look like 12.5, 01:23.4 or 1:02:03.",
                    n=i,
                )
                return None
            ee = min(ee, total) if total else ee
            if ee <= ss:
                self._warn("splitter.empty_range", "Clip {n} ends before it starts.", n=i)
                return None
            out = f"{base}{data['suffix'].get().strip() or f'_clip{i}'}{ext}"
            key = os.path.normcase(os.path.abspath(out))
            if key in seen:
                self._warn(
                    "splitter.duplicate_name",
                    "Two clips would both be saved as\n{p}\nGive them different suffixes.",
                    p=out,
                )
                return None
            seen.add(key)
            jobs.append((ss, ee, out))
        return jobs

    def _overwrite_or_rename(
        self, jobs: list[tuple[float, float, str]]
    ) -> list[tuple[float, float, str]] | None:
        """Ask once when outputs exist: overwrite, new names, or stop (None)."""
        existing = [o for _, _, o in jobs if os.path.exists(o)]
        if not existing:
            return jobs
        ans = messagebox.askyesnocancel(
            _tr("splitter.exists_title", "Files already exist"),
            _tr(
                "splitter.exists_msg",
                "{n} of the output files already exist.\n\nYes = overwrite them\nNo = save with new names\nCancel = stop",
            ).format(n=len(existing)),
            parent=self,
        )
        if ans is None:
            return None
        return jobs if ans else [(a, b, outputs.unique_path(o)) for a, b, o in jobs]

    def _report(self, results: list[tuple[str, str | None]]) -> None:
        ok = [o for o, e in results if e is None]
        bad = [(o, e) for o, e in results if e is not None]
        if not bad:
            names = "\n".join(os.path.basename(o) for o in ok)
            messagebox.showinfo(
                t("msg.done_title"), t("msg.batch_split_done") + "\n\n" + names, parent=self
            )
            return
        head = _tr("splitter.partial", "{ok} clip(s) saved, {bad} failed:").format(
            ok=len(ok), bad=len(bad)
        )
        lines = "\n".join(f"{os.path.basename(o)}: {e.splitlines()[-1]}" for o, e in bad)
        messagebox.showwarning(t("msg.done_title"), head + "\n\n" + lines, parent=self)

    def _render_all(self):
        if not self.file_path:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        if not self.split_rows:
            return
        info = ff.try_probe(self.file_path)
        base, src_ext = os.path.splitext(self.file_path)
        copy = bool(self.copy_mode.get())
        ext = src_ext if copy else ".mp4"
        jobs = self._clips(info.duration if info else 0.0, base, ext)
        jobs = self._overwrite_or_rename(jobs) if jobs is not None else None
        if jobs is None:
            return
        src = self.file_path
        results: list[tuple[str, str | None]] = []

        def work(
            progress_cb: Callable[[str], None], cancel_fn: Callable[[], bool], ctx: TaskContext
        ) -> int:
            n = len(jobs)
            for i, (ss, ee, out) in enumerate(jobs):
                if cancel_fn():
                    return -1
                key = _copy_start(src, ss) if copy else None
                cmd, seconds = clip_command(src, ss, ee, ext, keyframe=key)
                part = os.path.splitext(out)[0] + ".part" + os.path.splitext(out)[1]
                progress_cb(
                    f"\u25b6 {i + 1}/{n}: {ff.format_time(ss)} \u2013 {ff.format_time(ee)} \u2192 {os.path.basename(out)}"
                )
                res = ctx.span(i / n, (i + 1) / n).run([*cmd, part], duration=seconds, check=False)
                if (
                    res.ok
                    and not res.cancelled
                    and os.path.exists(part)
                    and os.path.getsize(part) > 0
                ):
                    os.replace(part, out)
                    results.append((out, None))
                    continue
                if os.path.exists(part):
                    os.remove(part)
                if res.cancelled:
                    return -1
                results.append((out, res.error_summary))
            return 0 if all(err is None for _, err in results) else 1

        label = self.btn_render.cget("text")
        self.btn_render.config(state="disabled")

        def done(_task_id: str, rc: int) -> None:
            self.btn_render.config(state="normal", text=label)
            if rc != -1:
                self._report(results)

        self.enqueue_render(
            "Multi-Splitter",
            "",
            work=work,
            inputs=[src],
            hooks=Hooks(
                on_progress=lambda _tid, line: self.log(self.console, line), on_complete=done
            ),
        )
