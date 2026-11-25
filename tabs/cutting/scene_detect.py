"""
tabs/cutting/scene_detect.py  -  Scene Detector

Find the cuts in a video, review them in a list (click to preview), then
split the video into one file per scene, save the list, or embed the
scenes as chapters.

Splitting re-encodes by default so every file starts exactly on its cut
(the encoder is told to place a keyframe there); lossless splitting is
offered too, with cuts moving to the next keyframe.
"""

from __future__ import annotations

import csv
import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.ops import scenes
from core.ops.shared import chapters_cmd
from core.queue_manager import Hooks
from core.scaling import px
from tabs import common
from tabs.base_tab import BaseTab
from tabs.widgets import EncoderPanel, FileRow, FolderRow, FramePreview, scroll_body

if TYPE_CHECKING:
    from core.queue_manager import TaskContext


class SceneDetectTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info: ff.MediaInfo | None = None
        self.scenes: list[tuple[float, float]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        self.make_header(
            self,
            t("tab.scene_detector"),
            t("scene_detect.find_every_cut_then_split_list_or"),
            icon="🎬",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))
        self._build_detection(body)
        self._build_scene_list(body)
        self._build_actions(body)

    def _build_detection(self, body: tk.Frame) -> None:
        """Sensitivity, shortest scene and the Find button."""
        det = tk.Frame(body)
        det.pack(fill="x", padx=px(20), pady=px(4))
        tk.Label(det, text=t("scene_detect.sensitivity_label")).pack(side="left")
        self.sens_var = tk.DoubleVar(value=0.3)
        tk.Scale(
            det,
            variable=self.sens_var,
            from_=0.05,
            to=0.8,
            resolution=0.05,
            orient="horizontal",
            length=px(160),
        ).pack(side="left", padx=px(4))
        tk.Label(det, text=t("scene_detect.lower_finds_more_cuts_shortest_scene_s")).pack(
            side="left"
        )
        self.min_var = tk.StringVar(value="1.0")
        tk.Entry(det, textvariable=self.min_var, width=5).pack(side="left", padx=px(4))
        self.btn_detect = tk.Button(
            det,
            text=t("scene_detect.find_scenes"),
            relief="flat",
            cursor="hand2",
            command=self._detect,
        )
        self.btn_detect.pack(side="left", padx=px(10))

    def _build_scene_list(self, body: tk.Frame) -> None:
        """The scenes found, with a preview of the one chosen."""
        mid = tk.Frame(body)
        mid.pack(fill="x", padx=px(20), pady=px(4))
        self.tree = ttk.Treeview(
            mid, columns=("n", "start", "end", "len"), show="headings", height=9
        )
        for c, label, w in (
            ("n", "#", 40),
            ("start", "Start", 110),
            ("end", "End", 110),
            ("len", "Length", 90),
        ):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=px(w), anchor="w")
        self.tree.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._selected())
        self.preview = FramePreview(mid, 400, 225)
        self.preview.pack(side="left", padx=px(10))

    def _build_actions(self, body: tk.Frame) -> None:
        """Split into files, add chapters, or save the list."""
        act = tk.LabelFrame(body, text=t("scene_detect.use_the_scenes"), padx=px(12), pady=px(8))
        act.pack(fill="x", padx=px(20), pady=px(6))
        r = tk.Frame(act)
        r.pack(fill="x")
        self.lossless_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r,
            text=t("scene_detect.lossless_split_fast_but_each_cut_moves"),
            variable=self.lossless_var,
        ).pack(side="left")
        self.encoder = EncoderPanel(act, codecs=("h264", "hevc", "av1"))
        self.encoder.pack(anchor="w", pady=px(4))
        self.dir_var = tk.StringVar()
        FolderRow(
            act, t("scene_detect.save_files_to"), self.dir_var, width=50, label_width=14
        ).pack(fill="x")
        btns = tk.Frame(act)
        btns.pack(pady=px(8, 0))
        self.btn_split = self.make_render_btn(
            btns, t("scene_detect.split_into_scenes"), self._split
        )
        self.btn_split.pack(side="left", padx=px(4))
        self.btn_chapters = tk.Button(
            btns, text=t("scene_detect.add_as_chapters"), relief="flat", command=self._chapters
        )
        self.btn_chapters.pack(side="left", padx=px(4))
        tk.Button(
            btns, text=t("scene_detect.save_list_csv"), relief="flat", command=self._save_csv
        ).pack(side="left", padx=px(4))

        self.console = self.make_console_block(body, height=5)

    # ── detection ────────────────────────────────────────────────────────────

    def _source_changed(self, path: str) -> None:
        self._info = ff.try_probe(path)
        self.scenes = []
        self._fill()
        dur = self._info.duration if self._info else 0.0
        self.preview.load(path, dur, 0.0)
        if not self.dir_var.get().strip():
            from core.outputs import default_output_folder

            self.dir_var.set(default_output_folder(path))

    def _detect(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            min_len = common.number(
                self.min_var.get() or "0", t("scene_detect.shortest_scene"), 0, 3600
            )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        thr = float(self.sens_var.get())
        result: dict[str, list[float]] = {}

        def work(_p: object, _c: object, ctx: TaskContext) -> int:
            result["starts"] = scenes.detect(ctx, src, thr, min_len, info.duration)
            return 0

        orig = self.btn_detect.cget("command")

        def on_complete(tid: str, rc: int) -> None:
            self.btn_detect.config(text=t("scene_detect.find_scenes"), command=orig)
            if rc == 0:
                self.scenes = scenes.scene_ranges(result.get("starts", [0.0]), info.duration)
                self._fill()
                self.log_tagged(
                    self.console,
                    t("scene_detect.scenes_scenes_found", scenes=len(self.scenes)),
                    "success",
                )
            elif rc != -1:
                self.last_task = self.task(tid)
                self.show_result(rc)

        tid = self.enqueue_render(
            "Scene detection",
            "",
            work=work,
            expected_duration=info.duration,
            inputs=[src],
            hooks=Hooks(on_complete=on_complete, on_progress=lambda _t, line: None),
        )
        self.btn_detect.config(text=t("scene_detect.stop"), command=lambda: self.cancel_render(tid))

    def _fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, (a, b) in enumerate(self.scenes):
            self.tree.insert(
                "",
                "end",
                iid=str(i),
                values=(i + 1, ff.format_time(a), ff.format_time(b), f"{b - a:.2f}s"),
            )

    def _selected(self) -> None:
        sel = self.tree.selection()
        if sel:
            a, b = self.scenes[int(sel[0])]
            self.preview.show(a + min(0.2, (b - a) / 2))

    # ── actions ──────────────────────────────────────────────────────────────

    def _need_scenes(self) -> bool:
        if not self.scenes:
            messagebox.showwarning(
                t("common.warning"), t("scene_detect.find_the_scenes_first"), parent=self
            )
            return False
        return True

    def _split(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None or not self._need_scenes():
            return
        folder = self.dir_var.get().strip() or os.path.dirname(src)
        base = os.path.splitext(os.path.basename(src))[0]
        from core.outputs import fresh_folder

        out_dir = fresh_folder(folder, f"{base} scenes")
        ext = os.path.splitext(src)[1].lower() if self.lossless_var.get() else ".mp4"
        self.log(
            self.console,
            t(
                "scene_detect.splitting_into_scenes_files_out_dir",
                scenes=len(self.scenes),
                out_dir=out_dir,
            ),
        )
        if self.lossless_var.get():
            pattern = os.path.join(out_dir, base).replace("%", "%%") + f"_scene_%03d{ext}"
            self.run_ffmpeg(
                scenes.split_copy_cmd(src, self.scenes, pattern, info.duration),
                self.console,
                on_done=lambda rc: self.show_result(rc, out_dir),
                btn=self.btn_split,
                duration=info.duration,
                output=out_dir,
                inputs=[src],
                name="Scene split",
            )
            return
        # Re-encode: one exact cut per scene, whatever the encoder's keyframe habits.
        name = os.path.join(out_dir, base).replace("%", "%%") + "_scene_%03d.mp4"
        venc = self.encoder.video_encode("mp4")
        cmds = scenes.split_encode_cmds(src, self.scenes, name, venc, has_audio=info.has_audio)
        self.run_ffmpeg_steps(
            cmds,
            self.console,
            on_done=lambda rc: self.show_result(rc, out_dir),
            btn=self.btn_split,
            output=out_dir,
            inputs=[src],
            name="Scene split",
        )

    def _chapters(self) -> None:
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None or not self._need_scenes():
            return
        ext = os.path.splitext(src)[1].lower()
        if ext not in (".mp4", ".mkv", ".mov", ".m4v"):
            ext = ".mkv"
        out = self.confirm_output(self.suggest_output(src, "chapters", ext), [src], default_ext=ext)
        if not out:
            return
        from core.paths import temp_dir

        meta = fl.write_ffmetadata(
            os.path.join(temp_dir("chapters_"), "meta.txt"),
            chapters=[(a, b, f"Scene {i + 1}") for i, (a, b) in enumerate(self.scenes)],
        )
        cmd = chapters_cmd(src, meta, out)
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_chapters,
            duration=info.duration,
            output=out,
            inputs=[src],
            name="Add chapters",
        )

    def _save_csv(self) -> None:
        if not self._need_scenes():
            return
        from tabs.kit import dialogs

        base = os.path.splitext(os.path.basename(self.src_row.path))[0]
        out = dialogs.ask_save(
            f"{base}_scenes.csv", kind="text", defaultextension=".csv", parent=self
        )
        if not out:
            return
        with open(out, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["scene", "start", "end", "start_seconds", "end_seconds", "length_seconds"])
            for i, (a, b) in enumerate(self.scenes, 1):
                w.writerow(
                    [
                        i,
                        ff.format_time(a),
                        ff.format_time(b),
                        f"{a:.3f}",
                        f"{b:.3f}",
                        f"{b - a:.3f}",
                    ]
                )
        self.log_tagged(self.console, t("scene_detect.saved_out", out=out), "success")
