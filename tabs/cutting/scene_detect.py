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
import re
import tkinter as tk
from tkinter import messagebox, ttk

from core import encoders as enc
from core import ffmpeg as ff
from core import filters as fl
from core.i18n import t
from core.scaling import px
from tabs import common
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.social.frame_extractor import keep_scenes
from tabs.widgets import EncoderPanel, FileRow, FramePreview, scroll_body


def scene_ranges(starts, duration: float) -> list:
    """[(start, end)] for each scene."""
    ends = [*list(starts[1:]), duration]
    return [(a, b) for a, b in zip(starts, ends, strict=False) if b - a > 0.01]


class SceneDetectTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.src_var = tk.StringVar()
        self._info = None
        self.scenes: list = []
        self._build_ui()

    def _build_ui(self):
        self.make_header(
            self,
            t("tab.scene_detector"),
            "Find every cut, then split, list or chapter the video.",
            icon="🎬",
        )
        body = scroll_body(self)
        self.src_row = FileRow(
            body, t("common.source_video"), self.src_var, on_change=self._source_changed
        )
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        det = tk.Frame(body)
        det.pack(fill="x", padx=px(20), pady=px(4))
        tk.Label(det, text="Sensitivity:").pack(side="left")
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
        tk.Label(det, text="(lower finds more cuts)   Shortest scene (s):").pack(side="left")
        self.min_var = tk.StringVar(value="1.0")
        tk.Entry(det, textvariable=self.min_var, width=5).pack(side="left", padx=px(4))
        self.btn_detect = tk.Button(
            det, text="🔍 Find scenes", relief="flat", cursor="hand2", command=self._detect
        )
        self.btn_detect.pack(side="left", padx=px(10))

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

        act = tk.LabelFrame(body, text="  Use the scenes  ", padx=px(12), pady=px(8))
        act.pack(fill="x", padx=px(20), pady=px(6))
        r = tk.Frame(act)
        r.pack(fill="x")
        self.lossless_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r,
            text="Lossless split (fast, but each cut moves to the next keyframe)",
            variable=self.lossless_var,
        ).pack(side="left")
        self.encoder = EncoderPanel(act, codecs=("h264", "hevc", "av1"))
        self.encoder.pack(anchor="w", pady=px(4))
        drow = tk.Frame(act)
        drow.pack(fill="x")
        tk.Label(drow, text="Save files to:", width=14, anchor="w").pack(side="left")
        self.dir_var = tk.StringVar()
        tk.Entry(drow, textvariable=self.dir_var, width=50).pack(
            side="left", fill="x", expand=True, padx=px(6)
        )
        tk.Button(drow, text=t("btn.browse"), relief="flat", command=self._pick_dir).pack(
            side="left"
        )
        btns = tk.Frame(act)
        btns.pack(pady=px(8, 0))
        self.btn_split = tk.Button(
            btns,
            text="✂  SPLIT INTO SCENES",
            font=(UI_FONT, 11, "bold"),
            bg=CLR["green"],
            fg="white",
            cursor="hand2",
            command=self._split,
        )
        self.btn_split.pack(side="left", padx=px(4))
        self.btn_chapters = tk.Button(
            btns, text="📑 Add as chapters…", relief="flat", command=self._chapters
        )
        self.btn_chapters.pack(side="left", padx=px(4))
        tk.Button(btns, text="💾 Save list (CSV)…", relief="flat", command=self._save_csv).pack(
            side="left", padx=px(4)
        )

        cf = tk.Frame(body)
        cf.pack(fill="both", expand=True, padx=px(20), pady=px(0, 10))
        self.console, csb = self.make_console(cf, height=5)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

    # ── detection ────────────────────────────────────────────────────────────

    def _source_changed(self, path):
        self._info = ff.try_probe(path)
        self.scenes = []
        self._fill()
        dur = self._info.duration if self._info else 0.0
        self.preview.load(path, dur, 0.0)
        if not self.dir_var.get().strip():
            from core.outputs import default_output_folder

            self.dir_var.set(default_output_folder(path))

    def _pick_dir(self):
        from tabs.kit import dialogs

        d = dialogs.ask_directory(parent=self)
        if d:
            self.dir_var.set(d)

    def _detect(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None:
            return
        try:
            min_len = common.number(self.min_var.get() or "0", "Shortest scene", 0, 3600)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        thr = float(self.sens_var.get())
        result = {}

        def work(_p, _c, ctx):
            tmp = ctx.tempdir()
            ctx.run(
                [
                    ff.ffmpeg_path(),
                    "-i",
                    src,
                    "-an",
                    "-sn",
                    "-dn",
                    "-vf",
                    f"scale=320:-2,select='gt(scene,{thr:.3f})',metadata=print:file=cuts.txt",
                    "-fps_mode",
                    "passthrough",
                    "-f",
                    "null",
                    "-",
                ],
                duration=info.duration,
                cwd=tmp,
            )
            try:
                with open(os.path.join(tmp, "cuts.txt"), encoding="utf-8", errors="replace") as fh:
                    cuts = [float(m) for m in re.findall(r"pts_time:\s*([\d.]+)", fh.read())]
            except OSError:
                cuts = []
            result["starts"] = keep_scenes(set(cuts), min_len)
            return 0

        orig = self.btn_detect.cget("command")

        def on_complete(tid, rc):
            self.btn_detect.config(text="🔍 Find scenes", command=orig)
            if rc == 0:
                self.scenes = scene_ranges(result.get("starts", [0.0]), info.duration)
                self._fill()
                self.log_tagged(self.console, f"{len(self.scenes)} scenes found.", "success")
            elif rc != -1:
                self.last_task = self.task(tid)
                self.show_result(rc)

        tid = self.enqueue_render(
            "Scene detection",
            "",
            worker_fn=work,
            on_complete=on_complete,
            on_progress=lambda _t, line: None,
            expected_duration=info.duration,
            inputs=[src],
        )
        self.btn_detect.config(text="✖ Stop", command=lambda: self.cancel_render(tid))

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, (a, b) in enumerate(self.scenes):
            self.tree.insert(
                "",
                "end",
                iid=str(i),
                values=(i + 1, ff.format_time(a), ff.format_time(b), f"{b - a:.2f}s"),
            )

    def _selected(self):
        sel = self.tree.selection()
        if sel:
            a, b = self.scenes[int(sel[0])]
            self.preview.show(a + min(0.2, (b - a) / 2))

    # ── actions ──────────────────────────────────────────────────────────────

    def _need_scenes(self):
        if not self.scenes:
            messagebox.showwarning(t("common.warning"), "Find the scenes first.", parent=self)
            return False
        return True

    def _split(self):
        src = self.src_row.path
        info = common.check_source(self, src)
        if info is None or not self._need_scenes():
            return
        folder = self.dir_var.get().strip() or os.path.dirname(src)
        base = os.path.splitext(os.path.basename(src))[0]
        from tabs.social.frame_extractor import fresh_folder

        out_dir = fresh_folder(folder, f"{base} scenes")
        ext = os.path.splitext(src)[1].lower() if self.lossless_var.get() else ".mp4"
        self.log(self.console, f"Splitting into {len(self.scenes)} files → {out_dir}")
        if self.lossless_var.get():
            pattern = os.path.join(out_dir, base).replace("%", "%%") + f"_scene_%03d{ext}"
            times = ",".join(f"{a:.3f}" for a, _b in self.scenes[1:]) or f"{info.duration + 1:.3f}"
            cmd = [
                ff.ffmpeg_path(),
                "-y",
                "-i",
                src,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_times",
                times,
                "-reset_timestamps",
                "1",
            ]
            if ext in (".mp4", ".mov", ".m4v"):
                cmd += ["-segment_format_options", "movflags=+faststart"]
            self.run_ffmpeg(
                [*cmd, pattern],
                self.console,
                on_done=lambda rc: self.show_result(rc, out_dir),
                btn=self.btn_split,
                btn_label="✂  SPLIT INTO SCENES",
                duration=info.duration,
                output=out_dir,
                inputs=[src],
                name="Scene split",
            )
            return
        # Re-encode: one exact cut per scene, whatever the encoder's keyframe habits.
        venc = self.encoder.video_encode("mp4")
        audio = enc.audio("aac", container="mp4") if info.has_audio else []
        cmds = []
        for i, (a, b) in enumerate(self.scenes, 1):
            cmds.append(
                [
                    ff.ffmpeg_path(),
                    "-y",
                    *venc.pre_input,
                    "-ss",
                    ff.ts(a),
                    "-t",
                    ff.ts(b - a),
                    "-i",
                    src,
                    "-map",
                    "0:v:0",
                    *(["-map", "0:a:0"] if info.has_audio else ["-an"]),
                    *venc.vf_args(None),
                    *venc.args,
                    *audio,
                    os.path.join(out_dir, f"{base}_scene_{i:03d}.mp4"),
                ]
            )
        self.run_ffmpeg(
            None,
            self.console,
            on_done=lambda rc: self.show_result(rc, out_dir),
            btn=self.btn_split,
            btn_label="✂  SPLIT INTO SCENES",
            output=out_dir,
            inputs=[src],
            name="Scene split",
            cmds=cmds,
        )

    def _chapters(self):
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
        cmd = [
            ff.ffmpeg_path(),
            "-y",
            "-i",
            src,
            "-f",
            "ffmetadata",
            "-i",
            meta,
            "-map",
            "0",
            "-map_metadata",
            "0",
            "-map_chapters",
            "1",
            "-c",
            "copy",
            out,
        ]
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_chapters,
            btn_label="📑 Add as chapters…",
            duration=info.duration,
            output=out,
            inputs=[src],
            name="Add chapters",
        )

    def _save_csv(self):
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
        self.log_tagged(self.console, f"Saved {out}", "success")
