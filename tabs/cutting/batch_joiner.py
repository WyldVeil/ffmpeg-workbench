"""
tab_batchjoiner.py  ─  Batch Joiner
Drag-and-drop queue of video files, optionally re-encode to a uniform
spec, then concatenate them all in order into a single output file.
"""

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import concat
from core import encoders as enc
from core import ffmpeg as ff
from core.i18n import t
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.widgets import SaveRow


def _tr(key, fallback):
    text = t(key)
    return fallback if text == key else text


class BatchJoinerTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self.file_paths = []
        self._build_ui()

    def _build_ui(self):
        opt_lf = self._build_listbox()
        self._build_reencode(opt_lf)
        self._build_out()

    def _build_listbox(self):
        make_header(self, t("tab.batch_joiner"), t("batch_joiner.desc"), icon="📎")

        # File list
        lf = tk.LabelFrame(self, text=t("batch_joiner.sect_video_queue"), padx=px(10), pady=px(6))
        lf.pack(fill="both", expand=True, padx=px(20), pady=px(8))

        self.listbox = tk.Listbox(
            lf,
            selectmode="single",
            font=(MONO_FONT, 10),
            relief="flat",
            bd=px(1),
            height=14,
            activestyle="dotbox",
            bg=CLR["bg"],
            fg=CLR["fg"],
            selectbackground=CLR["accent"],
            selectforeground="black",
        )
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Buttons row
        btn_f = tk.Frame(self)
        btn_f.pack(pady=px(5))
        for txt, cmd, bg in [
            (t("batch_joiner.btn_add_files"), self._add_files, "#3A3A3A"),
            (t("batch_joiner.btn_add_folder"), self._add_folder, "#3A3A3A"),
            (t("batch_joiner.btn_move_up"), self._move_up, "#3A3A3A"),
            (t("batch_joiner.btn_move_down"), self._move_down, "#3A3A3A"),
            (t("batch_joiner.btn_remove"), self._remove_sel, "#3A3A3A"),
            (t("batch_joiner.btn_clear_all"), self._clear, "#3A3A3A"),
        ]:
            tk.Button(btn_f, text=txt, bg=bg, fg=CLR["fg"], width=13, command=cmd).pack(
                side="left", padx=px(4)
            )

        # Options
        opt_lf = tk.LabelFrame(self, text=t("section.output_options"), padx=px(15), pady=px(8))
        opt_lf.pack(fill="x", padx=px(20), pady=px(5))
        return opt_lf

    def _build_reencode(self, opt_lf):
        # Row 1
        r1 = tk.Frame(opt_lf)
        r1.pack(fill="x", pady=px(3))
        self.reencode_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r1,
            text=t("batch_joiner.opt_reencode"),
            variable=self.reencode_var,
            command=self._toggle_encode_opts,
        ).pack(side="left")

        # Row 2 - encode options (shown/hidden)
        self.enc_frame = tk.Frame(opt_lf)
        tk.Label(self.enc_frame, text=t("common.resolution")).pack(side="left")
        self._res_choices = [
            ("first", _tr("batch_joiner.same_as_first", "Same as first clip")),
            ("largest", _tr("batch_joiner.largest_clip", "Largest clip")),
            ("3840x2160", "3840x2160"),
            ("2560x1440", "2560x1440"),
            ("1920x1080", "1920x1080"),
            ("1280x720", "1280x720"),
            ("854x480", "854x480"),
        ]
        self.res_var = tk.StringVar(value=self._res_choices[0][1])
        ttk.Combobox(
            self.enc_frame,
            textvariable=self.res_var,
            width=18,
            values=[lbl for _, lbl in self._res_choices],
            state="readonly",
        ).pack(side="left", padx=px(4))
        tk.Label(self.enc_frame, text=t("batch_joiner.lbl_fps")).pack(side="left")
        self._fps_choices = [
            ("first", _tr("batch_joiner.same_as_first", "Same as first clip")),
            ("largest", _tr("batch_joiner.highest", "Highest")),
            ("24", "24"),
            ("25", "25"),
            ("30", "30"),
            ("50", "50"),
            ("60", "60"),
        ]
        self.fps_var = tk.StringVar(value=self._fps_choices[0][1])
        ttk.Combobox(
            self.enc_frame,
            textvariable=self.fps_var,
            width=18,
            values=[lbl for _, lbl in self._fps_choices],
            state="readonly",
        ).pack(side="left", padx=px(4))
        tk.Label(self.enc_frame, text=t("common.crf")).pack(side="left")
        self.crf_var = tk.StringVar(value="18")
        tk.Entry(self.enc_frame, textvariable=self.crf_var, width=4, relief="flat").pack(
            side="left", padx=px(4)
        )
        tk.Label(self.enc_frame, text=t("batch_joiner.lbl_audio")).pack(side="left")
        self.audio_var = tk.StringVar(value="192k")
        ttk.Combobox(
            self.enc_frame,
            textvariable=self.audio_var,
            width=7,
            values=["96k", "128k", "192k", "256k", "320k"],
            state="readonly",
        ).pack(side="left", padx=px(4))

    def _build_out(self):
        # Output
        self.out_var = tk.StringVar()
        SaveRow(self, t("common.output_file"), self.out_var).pack(pady=px(5), fill="x")

        # Render
        self.btn_render = self.make_render_btn(
            self, t("batch_joiner.btn_join"), self._render, width=30
        )
        self.btn_render.pack(pady=px(8))

        # Console
        self.console = self.make_console_block(self, height=6, padx=px(20), pady=px(4))

    def _toggle_encode_opts(self):
        if self.reencode_var.get():
            self.enc_frame.pack(fill="x", pady=px(4))
        else:
            self.enc_frame.pack_forget()

    def _add_files(self):
        paths = dialogs.askopenfilenames(
            filetypes=[
                ("Video", "*.mp4 *.mov *.mkv *.avi *.webm *.flv"),
                ("All", t("ducker.item_2")),
            ]
        )
        for p in paths:
            if p not in self.file_paths:
                self.file_paths.append(p)
                self.listbox.insert(tk.END, f"  {os.path.basename(p)}")

    def _add_folder(self):
        folder = dialogs.askdirectory()
        if not folder:
            return
        exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}
        for f in sorted(os.listdir(folder)):
            if os.path.splitext(f)[1].lower() in exts:
                full = os.path.join(folder, f)
                if full not in self.file_paths:
                    self.file_paths.append(full)
                    self.listbox.insert(tk.END, f"  {f}")

    def _move_up(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self.file_paths[i], self.file_paths[i - 1] = self.file_paths[i - 1], self.file_paths[i]
        txt = self.listbox.get(i)
        prev = self.listbox.get(i - 1)
        self.listbox.delete(i - 1, i)
        self.listbox.insert(i - 1, txt)
        self.listbox.insert(i, prev)
        self.listbox.selection_set(i - 1)

    def _move_down(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= self.listbox.size() - 1:
            return
        i = sel[0]
        self.file_paths[i], self.file_paths[i + 1] = self.file_paths[i + 1], self.file_paths[i]
        txt = self.listbox.get(i)
        nxt = self.listbox.get(i + 1)
        self.listbox.delete(i, i + 1)
        self.listbox.insert(i, nxt)
        self.listbox.insert(i + 1, txt)
        self.listbox.selection_set(i + 1)

    def _remove_sel(self):
        sel = self.listbox.curselection()
        if sel:
            i = sel[0]
            self.file_paths.pop(i)
            self.listbox.delete(i)

    def _clear(self):
        self.file_paths.clear()
        self.listbox.delete(0, tk.END)

    def _choice(self, var, choices):
        return {lbl: key for key, lbl in choices}.get(var.get(), choices[0][0])

    def load_file(self, path):
        self.file_paths.append(path)
        self.listbox.insert(tk.END, os.path.basename(path))
        if not self.out_var.get().strip():
            self.out_var.set(self.suggest_output(path, "joined", ".mp4"))
        return True

    def _render(self):
        if len(self.file_paths) < 2:
            messagebox.showwarning(
                t("common.warning"), t("batch_joiner.msg_too_few_files"), parent=self
            )
            return
        out = self.out_var.get().strip() or self.suggest_output(
            self.file_paths[0], "joined", ".mp4"
        )
        out = self.confirm_output(out, self.file_paths)
        if not out:
            return
        self.out_var.set(out)
        try:
            pieces = [concat.Piece.of(p) for p in self.file_paths]
        except ff.ProbeError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)
            return
        total = sum(p.length for p in pieces)
        if not self.reencode_var.get():
            problems = concat.copy_compatibility([p.info for p in pieces])
            container = os.path.splitext(out)[1].lstrip(".").lower()
            src_ext = os.path.splitext(self.file_paths[0])[1].lstrip(".").lower()
            if (
                not problems
                and container in ("mp4", "mov", "m4v")
                and src_ext not in ("mp4", "mov", "m4v")
                and pieces[0].info.audio
                and not enc.audio_ok_for(
                    container, enc.audio_key_from_probe(pieces[0].info.require_audio().codec) or "x"
                )
            ):
                problems.append(
                    f"{pieces[0].info.require_audio().codec} audio can't be stored in .{container}"
                )
            if problems:
                msg = (
                    _tr(
                        "batch_joiner.not_copyable",
                        "These files can't be joined losslessly because they differ:",
                    )
                    + "\n\n"
                    + "\n".join("\u2022 " + p for p in problems[:8])
                    + "\n\n"
                    + _tr(
                        "batch_joiner.offer_reencode",
                        "Re-encode them so they match? (slower, but the result will play correctly)",
                    )
                )
                if not messagebox.askyesno(
                    _tr("batch_joiner.files_differ", "Files differ"), msg, parent=self
                ):
                    return
                self.reencode_var.set(True)
                self._toggle_encode_opts()
            else:
                from core.paths import temp_dir

                list_path = os.path.join(temp_dir("join_"), "list.txt")
                cmd = concat.copy_join_command(self.file_paths, out, list_path)
                self.log(
                    self.console,
                    f"Lossless join of {len(pieces)} files \u2192 {os.path.basename(out)}",
                )
                self.run_ffmpeg(
                    cmd,
                    self.console,
                    on_done=lambda rc: self.show_result(rc, out),
                    btn=self.btn_render,
                    duration=total,
                    output=out,
                    inputs=self.file_paths,
                    name="Batch Joiner",
                )
                return
        res_mode = self._choice(self.res_var, self._res_choices)
        fps_mode = self._choice(self.fps_var, self._fps_choices)
        W, H, fps = concat.target_geometry(pieces, res_mode)
        if fps_mode == "largest":
            fps = concat.target_geometry(pieces, "largest")[2]
        elif fps_mode not in ("first",):
            fps = fps_mode
        container = os.path.splitext(out)[1].lstrip(".").lower() or "mp4"
        venc = enc.video(
            "vp9" if container == "webm" else "h264",
            "high",
            speed="balanced",
            hw="auto",
            container=container,
        )
        venc.set_quality(self.crf_var.get())
        cmd, total = concat.filter_join_command(
            pieces,
            out,
            size=(W, H),
            fps=fps,
            venc=venc,
            audio_codec="opus" if container == "webm" else "aac",
            audio_bitrate=self.audio_var.get() or "192k",
        )
        self.log(
            self.console, f"Re-encoding {len(pieces)} files to {W}x{H} @ {fps} fps ({venc.label})"
        )
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=total,
            output=out,
            inputs=self.file_paths,
            name="Batch Joiner",
        )
