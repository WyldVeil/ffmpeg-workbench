"""
tabs/transcoder/presets.py  -  Preset Manager

Named FFmpeg option templates with {INPUT} and {OUTPUT} placeholders: save,
organise, import/export as JSON and run on a file.

Templates are split into arguments and run directly - never through a shell -
so file names can't inject commands; shell operators (&&, |, >) are refused.
User presets live in the app's config folder.
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from core import ffmpeg as ff
from core.i18n import t
from core.ops import presets as pr
from core.scaling import px
from tabs.base_tab import CLR, MONO_FONT, UI_FONT, BaseTab, make_header
from tabs.kit import dialogs
from tabs.kit.tktyped import add_pane, linked_scrollbar, on_write
from tabs.widgets import SaveRow

#: the category filter's "show every category" entry (the others are the presets' own)
ALL_CATEGORIES = "All"


class PresetManagerTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.editor: dict[str, tk.StringVar] = {}  # the preset editor's fields, by name
        self.presets, warning = pr.load_presets()
        self._selected_idx: int | None = None
        self._build_ui()
        if warning:
            self.after(
                300, lambda: messagebox.showwarning(t("common.warning"), warning, parent=self)
            )

    def _build_ui(self) -> None:
        make_header(self, t("tab.preset_manager"), t("presets.subtitle"), icon="💾")

        # ── Main paned layout ─────────────────────────────────────────────
        paned = tk.PanedWindow(self, orient="horizontal", sashwidth=px(6), bg="#AAAAAA")
        paned.pack(fill="both", expand=True, padx=px(10), pady=px(8))

        left = tk.Frame(paned, width=px(420))
        right = tk.Frame(paned, width=px(560))
        add_pane(paned, left, px(280))
        add_pane(paned, right, px(320))

        self._build_list(left)
        self._build_editor(right)

        # Seed list
        self._refresh_list()

    # ── Preset list ───────────────────────────────────────────────────────
    def _build_list(self, parent: tk.Misc) -> None:
        tk.Label(parent, text="PRESETS", font=(UI_FONT, 9, "bold"), fg=CLR["fgdim"]).pack(
            anchor="w", padx=px(8), pady=px(6, 2)
        )

        # Filter / search
        search_f = tk.Frame(parent)
        search_f.pack(fill="x", padx=px(6), pady=px(2))
        tk.Label(search_f, text="🔍").pack(side="left")
        self.search_var = tk.StringVar()
        on_write(self.search_var, lambda: self._refresh_list())
        tk.Entry(search_f, textvariable=self.search_var, width=30).pack(side="left", padx=px(4))
        tk.Label(search_f, text=t("presets.category_label")).pack(side="left", padx=px(12, 4))
        self.cat_filter_var = tk.StringVar(value=ALL_CATEGORIES)
        self.cat_cb = ttk.Combobox(
            search_f,
            textvariable=self.cat_filter_var,
            values=[ALL_CATEGORIES],
            state="readonly",
            width=12,
        )
        self.cat_cb.pack(side="left")
        on_write(self.cat_filter_var, lambda: self._refresh_list())

        # Treeview
        cols = ("name", "category")
        self.tree = ttk.Treeview(
            parent, columns=cols, show="headings", selectmode="browse", height=20
        )
        self.tree.heading("name", text=t("presets.preset_name_col"))
        self.tree.heading("category", text=t("presets.category_col"))
        self.tree.column("name", width=px(270), stretch=True)
        self.tree.column("category", width=px(90))
        self.tree.tag_configure("builtin", foreground="#888888")
        self.tree.tag_configure("custom", foreground="#EEEEEE")

        tsb = linked_scrollbar(parent, self.tree)
        self.tree.pack(side="left", fill="both", expand=True, padx=px(6, 0))
        tsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-Button-1>", lambda e: self._quick_run())

        # Action buttons
        btn_f = tk.Frame(parent)
        btn_f.pack(fill="x", padx=px(6), pady=px(6))
        for txt, bg, cmd in [
            ("▶ Quick Run", CLR["green"], self._quick_run),
            ("🗑 Delete", "#3A3A3A", self._delete_selected),
            ("📋 Duplicate", "#3A3A3A", self._duplicate),
            ("📤 Export", "#3A3A3A", self._export_presets),
            ("📥 Import", "#3A3A3A", self._import_presets),
        ]:
            tk.Button(btn_f, text=txt, bg=bg, fg=CLR["fg"], font=(UI_FONT, 8), command=cmd).pack(
                side="left", padx=px(2)
            )

    # ── Editor panel ──────────────────────────────────────────────────────
    def _build_editor(self, parent: tk.Misc) -> None:
        ef = self._build_preset_form(parent)
        self._build_quick_run(ef)

    def _build_preset_form(self, parent: tk.Misc) -> tk.Frame:
        tk.Label(
            parent, text=t("presets.editor_section"), font=(UI_FONT, 9, "bold"), fg=CLR["fgdim"]
        ).pack(anchor="w", padx=px(8), pady=px(6, 2))

        ef = tk.Frame(parent)
        ef.pack(fill="both", expand=True, padx=px(8))

        fields = [
            (t("presets.name_label"), "ed_name_var", None),
            (
                t("presets.category_label"),
                "ed_cat_var",
                ["Social", "Web", "Compression", "Archive", "Proxy", "Audio", "Custom"],
            ),
            ("Description:", "ed_desc_var", None),
        ]

        for label, attr, choices in fields:
            row = tk.Frame(ef)
            row.pack(fill="x", pady=px(4))
            tk.Label(row, text=label, width=12, anchor="e").pack(side="left")
            var = tk.StringVar()
            self.editor[attr] = var
            if choices:
                ttk.Combobox(row, textvariable=var, values=choices, width=22).pack(
                    side="left", padx=px(6)
                )
            else:
                tk.Entry(row, textvariable=var, width=42, relief="flat").pack(
                    side="left", padx=px(6)
                )
        self._build_command_editor(ef)
        self._build_preset_buttons(ef)
        return ef

    def _build_command_editor(self, ef: tk.Frame) -> None:
        """The FFmpeg command, with snippets to insert."""
        # FFmpeg command template
        cmd_lbl = tk.Frame(ef)
        cmd_lbl.pack(fill="x", pady=px(8, 2))
        tk.Label(cmd_lbl, text=t("presets.command_label"), font=(UI_FONT, 9, "bold")).pack(
            side="left"
        )
        tk.Label(
            cmd_lbl,
            text=t("presets.use_input_and_output_as_placeholders"),
            fg=CLR["fgdim"],
            font=(UI_FONT, 8),
        ).pack(side="left")

        self.ed_cmd_text = scrolledtext.ScrolledText(
            ef, height=7, font=(MONO_FONT, 9), bg=CLR["bg"], fg="#00FF88", wrap="word"
        )
        self.ed_cmd_text.pack(fill="x", pady=px(2))

        # Help examples
        help_lf = tk.LabelFrame(
            ef, text=f"  {t('presets.quick_insert_section')}  ", padx=px(8), pady=px(4)
        )
        help_lf.pack(fill="x", pady=px(4))
        for label, snippet in pr.SNIPPETS:
            tk.Button(
                help_lf,
                text=label,
                bg="#333",
                fg=CLR["fg"],
                font=(UI_FONT, 8),
                command=lambda s=snippet: self._insert_snippet(s),
            ).pack(side="left", padx=px(2))

    def _build_preset_buttons(self, ef: tk.Frame) -> None:
        """Save, delete and test."""
        # Buttons
        act_f = tk.Frame(ef)
        act_f.pack(fill="x", pady=px(8))
        tk.Button(
            act_f,
            text=t("presets.save_button"),
            bg=CLR["accent"],
            fg="black",
            font=(UI_FONT, 10, "bold"),
            command=self._save_preset,
        ).pack(side="left", padx=px(4))
        tk.Button(
            act_f,
            text=t("presets.new_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=self._new_preset,
        ).pack(side="left", padx=px(4))
        tk.Button(
            act_f,
            text=t("presets.clear_button"),
            bg=CLR["panel"],
            fg=CLR["fg"],
            command=self._clear_editor,
        ).pack(side="left", padx=px(4))

    def _build_quick_run(self, ef: tk.Frame) -> None:
        """Quick Run section."""
        run_lf = tk.LabelFrame(
            ef, text=f"  {t('presets.quick_run_section')}  ", padx=px(10), pady=px(8)
        )
        run_lf.pack(fill="x", pady=px(6))

        run_row = tk.Frame(run_lf)
        run_row.pack(fill="x")
        tk.Label(run_row, text=t("webm_maker.input")).pack(side="left")
        self.run_src_var = tk.StringVar()
        tk.Entry(run_row, textvariable=self.run_src_var, width=36, relief="flat").pack(
            side="left", padx=px(4)
        )
        tk.Button(
            run_row, text="…", command=self._browse_run_src, width=2, cursor="hand2", relief="flat"
        ).pack(side="left")

        self.run_out_var = tk.StringVar()
        SaveRow(run_lf, t("encode_queue.output_label"), self.run_out_var, kind="media").pack(
            fill="x", pady=px(4)
        )

        self.btn_render = self.make_render_btn(run_lf, t("presets.run_now_button"), self._quick_run)
        self.btn_render.pack(pady=px(4), fill="x")

        # Console
        self.console = scrolledtext.ScrolledText(
            ef, height=6, bg=CLR["console_bg"], fg="#00FF88", font=(MONO_FONT, 8)
        )
        self.console.pack(fill="both", expand=True, pady=px(4))

    # ═══════════════════════════════════════════════════════════════════════
    #  List logic
    # ═══════════════════════════════════════════════════════════════════════
    def _refresh_list(self) -> None:
        search = self.search_var.get().lower()
        cat = self.cat_filter_var.get()

        # Update category dropdown
        cats = sorted({ALL_CATEGORIES} | {p.get("category", "Custom") for p in self.presets})
        self.cat_cb.config(values=cats)

        self.tree.delete(*self.tree.get_children())
        bi_names = {p["name"] for p in pr.BUILT_IN}
        for i, p in enumerate(self.presets):
            if (
                search
                and search not in p["name"].lower()
                and search not in p.get("description", "").lower()
            ):
                continue
            if cat != ALL_CATEGORIES and p.get("category", "Custom") != cat:
                continue
            tag = "builtin" if p["name"] in bi_names else "custom"
            self.tree.insert(
                "", tk.END, iid=str(i), values=(p["name"], p.get("category", "Custom")), tags=(tag,)
            )

    def _on_select(self, _: object = None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self._selected_idx = idx
        p = self.presets[idx]
        self.editor["ed_name_var"].set(p.get("name", ""))
        self.editor["ed_cat_var"].set(p.get("category", "Custom"))
        self.editor["ed_desc_var"].set(p.get("description", ""))
        self.ed_cmd_text.delete("1.0", tk.END)
        self.ed_cmd_text.insert(tk.END, p.get("cmd", ""))
        # Auto-fill output name hint
        src = self.run_src_var.get()
        if src:
            base = os.path.splitext(src)[0]
            self.run_out_var.set(base + "_preset.mp4")

    def _new_preset(self) -> None:
        self._selected_idx = None
        self._clear_editor()

    def _clear_editor(self) -> None:
        self.editor["ed_name_var"].set("")
        self.editor["ed_cat_var"].set("Custom")
        self.editor["ed_desc_var"].set("")
        self.ed_cmd_text.delete("1.0", tk.END)

    def _insert_snippet(self, s: str) -> None:
        self.ed_cmd_text.insert(tk.INSERT, " " + s + " ")

    def _save_preset(self) -> None:
        name = self.editor["ed_name_var"].get().strip()
        cmd = self.ed_cmd_text.get("1.0", tk.END).strip()
        if not name:
            messagebox.showwarning(t("common.warning"), t("presets.no_name_message"))
            return
        if not cmd:
            messagebox.showwarning(
                t("common.warning"), t("presets.no_command_message"), parent=self
            )
            return
        try:
            pr.check_template(cmd)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return

        entry = {
            "name": name,
            "category": self.editor["ed_cat_var"].get(),
            "description": self.editor["ed_desc_var"].get(),
            "cmd": cmd,
        }

        bi_names = {p["name"] for p in pr.BUILT_IN}
        if name in bi_names:
            messagebox.showwarning(
                t("common.warning"),
                t("presets.read_only_message"),
            )
            return

        # Update existing or append
        existing = next((i for i, p in enumerate(self.presets) if p["name"] == name), None)
        if existing is not None:
            self.presets[existing] = entry
        else:
            self.presets.append(entry)

        err = pr.save_user_presets(self.presets)
        self._refresh_list()
        if err:
            messagebox.showerror(
                t("common.error"),
                t("presets.the_preset_could_not_be_saved_err", err=err),
                parent=self,
            )
        else:
            messagebox.showinfo(
                t("presets.saved"), t("presets.preset_name_saved", name=name), parent=self
            )

    def _delete_selected(self) -> None:
        if self._selected_idx is None:
            return
        p = self.presets[self._selected_idx]
        bi_names = {p2["name"] for p2 in pr.BUILT_IN}
        if p["name"] in bi_names:
            messagebox.showwarning(t("common.warning"), t("presets.cannot_delete_builtin"))
            return
        if messagebox.askyesno(t("presets.delete"), t("presets.delete_preset_p", p=p["name"])):
            del self.presets[self._selected_idx]
            self._selected_idx = None
            err = pr.save_user_presets(self.presets)
            self._refresh_list()
            if err:
                messagebox.showerror(
                    t("common.error"),
                    t("presets.could_not_save_the_change_err", err=err),
                    parent=self,
                )

    def _duplicate(self) -> None:
        if self._selected_idx is None:
            return
        p = dict(self.presets[self._selected_idx])
        names = {x["name"] for x in self.presets}
        base, n = p["name"] + " (copy)", 2
        p["name"] = base
        while p["name"] in names:
            p["name"] = f"{base} {n}"
            n += 1
        self.presets.append(p)
        err = pr.save_user_presets(self.presets)
        self._refresh_list()
        if err:
            messagebox.showerror(
                t("common.error"), t("presets.could_not_save_the_change_err", err=err), parent=self
            )

    def _export_presets(self) -> None:
        path = dialogs.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        bi_names = {p["name"] for p in pr.BUILT_IN}
        mine = [p for p in self.presets if p["name"] not in bi_names] or self.presets
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(mine, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)
            return
        messagebox.showinfo(
            t("presets.exported"),
            t(
                "presets.mine_preset_s_exported_to_path",
                mine=len(mine),
                path=os.path.basename(path),
            ),
            parent=self,
        )

    def _import_presets(self) -> None:
        path = dialogs.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                new, skipped = pr.read_import(json.load(f), {p["name"] for p in self.presets})
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                t("common.error"), t("presets.could_not_read_the_file_exc", exc=exc), parent=self
            )
            return
        self.presets.extend(new)
        added = len(new)
        err = pr.save_user_presets(self.presets)
        self._refresh_list()
        msg = f"Added {added} new preset(s)."
        if skipped:
            msg += (
                f"\n\nSkipped {len(skipped)} that contain shell commands or no {{INPUT}}/{{OUTPUT}}:\n"
                + "\n".join(skipped[:10])
            )
        if err:
            msg += f"\n\nBut they could not be saved:\n{err}"
        messagebox.showinfo(t("presets.imported"), msg, parent=self)

    # ── Quick run ─────────────────────────────────────────────────────────
    def _browse_run_src(self) -> None:
        p = dialogs.askopenfilename(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", t("ducker.item_2"))]
        )
        if p:
            self.run_src_var.set(p)
            ext = ".mp4"
            if self._selected_idx is not None:
                ext = self.presets[self._selected_idx].get("ext") or ".mp4"
            self.run_out_var.set(self.suggest_output(p, "preset", ext))

    def _quick_run(self) -> None:
        if self._selected_idx is None:
            messagebox.showinfo(t("msg.no_preset_title"), t("msg.select_preset"), parent=self)
            return
        p = self.presets[self._selected_idx]
        src = self.run_src_var.get().strip()
        if not src:
            src = dialogs.askopenfilename(filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm")])
        if not src:
            return
        if not os.path.isfile(src):
            messagebox.showwarning(
                t("common.warning"), t("presets.file_not_found_src", src=src), parent=self
            )
            return
        ext = p.get("ext") or ".mp4"
        out = self.run_out_var.get().strip() or self.suggest_output(src, "preset", ext)
        out = self.confirm_output(out, [src], default_ext=ext)
        if not out:
            return
        self.run_src_var.set(src)
        self.run_out_var.set(out)
        try:
            cmd = pr.build_command(p.get("cmd", ""), src, out)
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        self.console.insert(
            tk.END, f"\n▶ Running preset: {p['name']}\n$ {' '.join(map(_quote, cmd))}\n\n"
        )
        self.console.see(tk.END)
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn_render,
            duration=ff.duration_of(src) or None,
            output=out,
            inputs=[src],
            name=f"Preset: {p['name']}",
        )


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg
