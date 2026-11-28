"""
tab_splicer.py  ─  The Splicer
Lets the user pick in/out points from multiple source videos and
stitches the chosen segments together into a single output file.
"""

from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox

from core import concat
from core import encoders as enc
from core import ffmpeg as ff
from core.hardware import get_video_duration
from core.i18n import t, t_or
from core.scaling import px
from tabs import framestep
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, VideoTimeline, make_header
from tabs.kit import dialogs
from tabs.kit.contracts import Transport
from tabs.kit.tktyped import on_write
from tabs.widgets import SaveRow, scroll_body


@dataclass(eq=False)
class _Segment:
    """One part of a source clip to splice in, with its row on the page."""

    path: str
    dur: float
    start: tk.StringVar
    end: tk.StringVar
    include: tk.BooleanVar
    row: tk.Frame | None = None
    dur_lbl: tk.Label | None = None


class SplicerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.segments: list[_Segment] = []
        self._sel_idx: int | None = None
        self._build_ui()

    # ─── UI ───────────────────────────────────────────────────────────────────

    # ── Contracts (tabs/kit/contracts.py): the playhead is the timeline's ──
    def transport(self) -> Transport | None:
        return self._timeline

    def mark_in(self) -> None:
        self._timeline.mark_in()

    def mark_out(self) -> None:
        self._timeline.mark_out()

    def _build_ui(self) -> None:
        btn_row = self._build_seg_canvas()
        self._build_out(btn_row)
        self._build_console()

    def _build_seg_canvas(self) -> tk.Frame:
        # Header
        make_header(self, t("tab.the_splicer"), t("splicer.subtitle"), icon="✂")

        # Segments list area
        list_outer = tk.LabelFrame(
            self, text=t("splicer.segment_queue_section"), padx=px(10), pady=px(8)
        )
        list_outer.pack(fill="both", expand=True, padx=px(20), pady=px(10))

        # Column headers
        hf = tk.Frame(list_outer)
        hf.pack(fill="x", pady=px(0, 4))
        for txt, w in [
            ("#", 3),
            (t("splicer.column_source"), 38),
            (t("splicer.column_start"), 8),
            (t("splicer.column_end"), 8),
            (t("splicer.column_duration"), 9),
            ("", 12),
        ]:
            tk.Label(hf, text=txt, font=(UI_FONT, 9, "bold"), width=w, anchor="w").pack(
                side="left", padx=px(2)
            )

        # Scrollable segment rows
        self.seg_frame = scroll_body(list_outer, height=280)

        # Timeline
        tl_frame = tk.LabelFrame(
            self, text=t("splicer.segment_queue_section") + " - Timeline", padx=px(10), pady=px(8)
        )
        tl_frame.pack(fill="x", padx=px(20), pady=px(0, 6))
        self._timeline = VideoTimeline(
            tl_frame, on_change=self._on_timeline_change, height=90, show_handles=True
        )
        self._timeline.pack(fill="x")
        self._tl_pos_var = tk.StringVar(value=t_or("splicer.sample.playhead", "Playhead: -"))
        tk.Label(
            tl_frame, textvariable=self._tl_pos_var, font=(MONO_FONT, 9), fg=CLR["fgdim"]
        ).pack(anchor="w", pady=px(4, 0))

        # Buttons
        btn_row = tk.Frame(self)
        return btn_row

    def _build_out(self, btn_row: tk.Frame) -> None:
        btn_row.pack(pady=px(8))
        tk.Button(
            btn_row,
            text=t("splicer.add_segment_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            width=18,
            command=self._add_segment,
        ).pack(side="left", padx=px(6))
        tk.Button(
            btn_row,
            text=t("splicer.remove_last_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            width=18,
            command=self._remove_last,
        ).pack(side="left", padx=px(6))
        tk.Button(
            btn_row,
            text=t("splicer.move_up_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            width=14,
            command=lambda: self._move(-1),
        ).pack(side="left", padx=px(6))
        tk.Button(
            btn_row,
            text=t("splicer.move_down_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            width=14,
            command=lambda: self._move(1),
        ).pack(side="left", padx=px(6))

        # Output row
        self.out_var = tk.StringVar()
        SaveRow(self, t("common.output_file"), self.out_var).pack(pady=px(5), fill="x")

        # Options
        opt_f = tk.Frame(self)
        opt_f.pack(pady=px(4))
        self.reencode_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt_f, text=t("splicer.reencode_checkbox"), variable=self.reencode_var).pack(
            side="left", padx=px(10)
        )
        tk.Label(opt_f, text=t("common.crf")).pack(side="left")
        self.crf_var = tk.StringVar(value="18")
        tk.Entry(opt_f, textvariable=self.crf_var, width=4, relief="flat").pack(
            side="left", padx=px(4)
        )

        # Render button
        self.btn_render = self.make_render_btn(
            self, t("splicer.splice_export_button"), self._render, width=30
        )

    def _build_console(self) -> None:
        self.btn_render.pack(pady=px(10))

        # Console
        self.console = self.make_console_block(self, height=7, padx=px(20), pady=px(5))

    # ─── Segment management ───────────────────────────────────────────────────
    def load_file(self, path: str) -> bool:
        """A dropped video is added as a segment."""
        if dialogs.kind_of(path) != "video":
            return False
        self._add_segment(path)
        return True

    def _add_segment(self, path: str = "") -> None:
        path = path or dialogs.askopenfilename(
            title=t("splicer.select_source_clip"),
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", t("ducker.item_2"))],
        )
        if not path:
            return
        dur = get_video_duration(path)
        idx = len(self.segments)
        seg = _Segment(
            path,
            dur,
            tk.StringVar(value="0"),
            tk.StringVar(value=f"{dur:.3f}"),
            tk.BooleanVar(value=True),
        )
        self.segments.append(seg)
        self._draw_row(idx, seg)
        self._select_row(idx)

    def _draw_row(self, idx: int, seg: _Segment) -> None:
        row = tk.Frame(self.seg_frame, relief="groove", bd=px(1))
        row.pack(fill="x", pady=px(2))
        seg.row = row

        tk.Label(row, text=str(idx + 1), width=3, anchor="w").pack(side="left", padx=px(2))
        fname = os.path.basename(seg.path)
        tk.Label(row, text=fname, width=38, anchor="w", fg=CLR["accent"]).pack(
            side="left", padx=px(2)
        )

        for var, tip in [(seg.start, "Start"), (seg.end, "End")]:
            tk.Label(row, text=tip + ":").pack(side="left")
            tk.Entry(row, textvariable=var, width=8, relief="flat").pack(side="left", padx=px(2))
            on_write(var, lambda: self._refresh_dur(seg))

        seg.dur_lbl = tk.Label(row, text=self._calc_dur(seg), width=9, fg=CLR["fgdim"])
        seg.dur_lbl.pack(side="left", padx=px(4))

        sel_btn = tk.Checkbutton(row, text=t("splicer.include"), variable=seg.include)
        sel_btn.pack(side="left", padx=px(4))
        row.bind("<Button-1>", lambda e, i=idx: self._select_row(i))

    def _calc_dur(self, seg: _Segment) -> str:
        try:
            d = float(seg.end.get()) - float(seg.start.get())
            return f"{d:.2f}s" if d > 0 else "⚠ invalid"
        except (tk.TclError, ValueError, TypeError):
            return "-"

    def _refresh_dur(self, seg: _Segment) -> None:
        if seg.dur_lbl is not None:
            seg.dur_lbl.config(text=self._calc_dur(seg))

    def _remove_last(self) -> None:
        if self.segments:
            seg = self.segments.pop()
            if seg.row is not None:
                seg.row.destroy()

    def _select_row(self, idx: int) -> None:
        if self._sel_idx is not None and self._sel_idx < len(self.segments):
            old = self.segments[self._sel_idx].row
            if old and old.winfo_exists():
                old.config(relief="groove", bd=px(1))
        self._sel_idx = idx
        new = self.segments[idx].row
        if new and new.winfo_exists():
            new.config(relief="solid", bd=px(2))
        self._update_timeline_for_segment(idx)

    def _on_timeline_change(self, start: float, end: float, playhead: float) -> None:
        """Called when the user interacts with the timeline widget."""
        self._tl_pos_var.set(f"Playhead: {playhead:.2f}s  |  Range: {start:.2f}s – {end:.2f}s")
        # Sync the timeline range back into the selected segment's in/out fields
        if self._sel_idx is not None and self._sel_idx < len(self.segments):
            seg = self.segments[self._sel_idx]
            seg.start.set(f"{start:.3f}")
            seg.end.set(f"{end:.3f}")

    def _update_timeline_for_segment(self, idx: int) -> None:
        """Push the selected segment's duration and range into the timeline."""
        if idx < len(self.segments):
            seg = self.segments[idx]
            self._timeline.set_duration(seg.dur, framestep.fps_of(seg.path))
            try:
                s = float(seg.start.get())
                e = float(seg.end.get())
            except ValueError:
                s, e = 0, seg.dur
            self._timeline.set_range(s, e)
            self._timeline.set_playhead(s)

    def _rebuild_rows(self) -> None:
        for seg in self.segments:
            if seg.row is not None and seg.row.winfo_exists():
                seg.row.destroy()
            seg.row = seg.dur_lbl = None
        for i, seg in enumerate(self.segments):
            self._draw_row(i, seg)

    def _move(self, direction: int) -> None:
        if self._sel_idx is None:
            messagebox.showinfo(t("splicer.select_row_title"), t("splicer.select_row_message"))
            return
        idx = self._sel_idx
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.segments):
            return
        self.segments[idx], self.segments[new_idx] = self.segments[new_idx], self.segments[idx]
        self._rebuild_rows()
        self._select_row(new_idx)

    # ─── Render ───────────────────────────────────────────────────────────────
    def _pieces(self, segs: list[_Segment]) -> list[concat.Piece]:
        pieces: list[concat.Piece] = []
        for i, seg in enumerate(segs, start=1):
            try:
                ss = ff.parse_time(seg.start.get())
                ee = ff.parse_time(seg.end.get())
            except ValueError:
                raise ValueError(
                    t_or(
                        "splicer.bad_time",
                        "Segment {n}: times must look like 12.5, 01:23.4 or 1:02:03.",
                    ).format(n=i)
                ) from None
            if ee <= ss:
                raise ValueError(
                    t_or("splicer.empty_segment", "Segment {n} ends before it starts.").format(n=i)
                )
            pieces.append(concat.Piece.of(seg.path, ss, ee))
        return pieces

    def _render(self) -> None:
        active = [s for s in self.segments if s.include.get()]
        if len(active) < 1:
            messagebox.showwarning(
                t("splicer.no_segments_title"), t("splicer.no_segments_message"), parent=self
            )
            return
        try:
            pieces = self._pieces(active)
        except (ValueError, ff.ProbeError) as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        inputs = sorted({p.path for p in pieces})
        out = self.out_var.get().strip() or self.suggest_output(pieces[0].path, "spliced", ".mp4")
        out = self.confirm_output(out, inputs)
        if not out:
            return
        self.out_var.set(out)
        container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
        if not self.reencode_var.get():
            problems = concat.copy_compatibility([p.info for p in pieces])
            if not problems:
                self._splice_copy(pieces, out, inputs)
                return
            if not self._agree_to_reencode(problems):
                return
            self.reencode_var.set(True)
        self._splice_reencode(pieces, out, inputs, container)

    def _agree_to_reencode(self, problems: list[str]) -> bool:
        return messagebox.askyesno(
            t_or("batch_joiner.files_differ", "Files differ"),
            t_or("splicer.not_copyable", "These segments can't be joined losslessly:")
            + "\n\n"
            + "\n".join("\u2022 " + x for x in problems[:8])
            + "\n\n"
            + t_or("batch_joiner.offer_reencode", "Re-encode them so they match?"),
            parent=self,
        )

    def _splice_copy(self, pieces: list[concat.Piece], out: str, inputs: list[str]) -> None:
        """Lossless: each segment from the keyframe at or before its start (said when it moves)."""
        from core.ops.trim import keyframe_at_or_before
        from core.paths import temp_dir

        ins: list[float] = []
        outs: list[float] = []
        for p in pieces:
            k = keyframe_at_or_before(p.path, p.start)
            k = p.start if k is None else k
            if p.start - k > 0.01:
                self.log(
                    self.console,
                    t(
                        "splicer.path_lossless_cut_starts_at_keyframe_k",
                        path=os.path.basename(p.path),
                        k=ff.format_time(k),
                        start=ff.format_time(p.start),
                    ),
                )
            ins.append(k)
            outs.append(p.start + p.length)
        list_path = os.path.join(temp_dir("splice_"), "list.txt")
        cmd = concat.copy_join_command(
            [p.path for p in pieces], out, list_path, inpoints=ins, outpoints=outs
        )
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._render_button(),
            duration=sum(o - i for i, o in zip(ins, outs, strict=False)),
            output=out,
            inputs=inputs,
            name="Splicer",
        )

    def _splice_reencode(
        self, pieces: list[concat.Piece], out: str, inputs: list[str], container: str
    ) -> None:
        """Every segment cut exactly and made alike, in one encode."""
        venc = enc.video(
            "vp9" if container == "webm" else "h264", "high", hw="auto", container=container
        )
        venc.set_quality(self.crf_var.get())
        cmd, total = concat.filter_join_command(
            pieces, out, venc=venc, audio_codec="opus" if container == "webm" else "aac"
        )
        self.log(
            self.console,
            t(
                "splicer.splicing_pieces_segments_total_label",
                pieces=len(pieces),
                total=ff.format_time(total, ms=False),
                label=venc.label,
            ),
        )
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self._render_button(),
            duration=total,
            output=out,
            inputs=inputs,
            name="Splicer",
        )

    def _render_button(self) -> tk.Button | None:
        for attr in ("btn_render", "render_btn", "_btn_render", "run_btn"):
            if hasattr(self, attr):
                return getattr(self, attr)
        return None

    def _render_label(self) -> str:
        btn = self._render_button()
        return btn.cget("text") if btn is not None else "Render"
