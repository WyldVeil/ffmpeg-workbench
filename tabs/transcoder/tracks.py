"""
tabs/transcoder/tracks.py  -  Tracks & Remux

Change what's inside a file without re-encoding (seconds, no quality loss):
keep, drop and reorder its tracks, set each one's language, title and
default/forced flags, add audio or subtitles from other files (with a
delay), save any single track as its own file, and move everything into
MKV, MP4, MOV or WebM - with a plain warning for whatever the chosen
container can't hold.  See core/remux.py.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from core import ffmpeg as ff
from core import remux
from core.i18n import t
from core.scaling import px
from tabs.base_tab import CLR, BaseTab
from tabs.kit import dialogs
from tabs.widgets import FileRow, SaveRow, output_box, scroll_body

CONTAINERS = {
    "mkv": "MKV - holds everything",
    "mp4": "MP4 - plays everywhere",
    "mov": "MOV - Apple / editing",
    "webm": "WebM - web",
}
LANG_CHOICES = [f"{name} ({code})" if code else name for code, name in remux.LANGUAGES]


def lang_label(code: str) -> str:
    name = remux.LANGUAGE_NAMES.get(code)
    return f"{name} ({code})" if name and code else (code or remux.LANGUAGE_NAMES[""])


def lang_code(text: str) -> str:
    text = text.strip()
    if not text or text == remux.LANGUAGE_NAMES[""]:
        return ""
    if text.endswith(")") and "(" in text:
        return text[text.rindex("(") + 1 : -1].strip()
    by_name = {name.lower(): code for code, name in remux.LANGUAGES if code}
    return by_name.get(text.lower(), text.lower()[:8])


class TracksRemuxTab(BaseTab):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.plan = remux.Plan()
        self._editing = False
        self._edit_index: int | None = None  # the track whose values the editor shows
        self._build_ui()

    def _build_ui(self) -> None:
        body, lf = self._build_header()
        cb, opt, r1 = self._build_output(body, lf)
        self._build_chapters(body, cb, opt, r1)

    def _build_header(self) -> tuple[tk.Frame, tk.LabelFrame]:
        self.make_header(
            self,
            "Tracks & Remux",
            "Keep, drop, reorder and label tracks, add audio or subtitles, "
            "change the container - without re-encoding.",
            icon="🗂",
        )
        body = scroll_body(self)
        self.src_row = FileRow(body, t("common.source_file"), on_change=self._source_changed)
        self.src_row.pack(fill="x", padx=px(20), pady=px(10, 4))

        lf = tk.LabelFrame(body, text="  Tracks (in output order)  ", padx=px(10), pady=px(8))
        lf.pack(fill="x", padx=px(20), pady=px(4))
        cols = ("keep", "kind", "what", "lang", "title", "flags", "from")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=9, selectmode="browse")
        for c, label, w in (
            ("keep", "Keep", 50),
            ("kind", "Type", 80),
            ("what", "Codec / details", 240),
            ("lang", "Language", 130),
            ("title", "Title", 170),
            ("flags", "Flags", 110),
            ("from", "From", 140),
        ):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=px(w), stretch=c in ("what", "title"))
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._selected())
        self.tree.bind("<Double-1>", lambda _e: self._toggle_keep())
        bar = tk.Frame(lf)
        bar.pack(fill="x", pady=px(6, 0))
        for text, cmd in (
            ("✓ / ✗  Keep", self._toggle_keep),
            ("▲", lambda: self._move(-1)),
            ("▼", lambda: self._move(1)),
            ("➕  Add audio or subtitles…", self._add_file),
            ("💾  Save track as a file…", self._extract),
        ):
            tk.Button(bar, text=text, relief="flat", cursor="hand2", command=cmd).pack(
                side="left", padx=px(0, 6)
            )
        return body, lf

    def _build_output(
        self, body: tk.Frame, lf: tk.LabelFrame
    ) -> tuple[ttk.Combobox, tk.LabelFrame, tk.Frame]:
        ed = tk.Frame(lf)
        ed.pack(fill="x", pady=px(8, 0))
        tk.Label(ed, text="Language:").pack(side="left")
        self.lang_var = tk.StringVar()
        lc = ttk.Combobox(ed, textvariable=self.lang_var, values=LANG_CHOICES, width=20)
        lc.pack(side="left", padx=px(4, 12))
        lc.bind("<<ComboboxSelected>>", lambda _e: self._apply_edit())
        lc.bind("<FocusOut>", lambda _e: self._apply_edit())
        lc.bind("<Return>", lambda _e: self._apply_edit())
        tk.Label(ed, text="Title:").pack(side="left")
        self.title_var = tk.StringVar()
        te = tk.Entry(ed, textvariable=self.title_var, width=26)
        te.pack(side="left", padx=px(4, 12))
        te.bind("<FocusOut>", lambda _e: self._apply_edit())
        te.bind("<Return>", lambda _e: self._apply_edit())
        self.default_var = tk.BooleanVar()
        self.forced_var = tk.BooleanVar()
        tk.Checkbutton(
            ed, text="Default", variable=self.default_var, command=self._apply_edit
        ).pack(side="left")
        tk.Checkbutton(ed, text="Forced", variable=self.forced_var, command=self._apply_edit).pack(
            side="left"
        )

        opt = output_box(body)
        r1 = tk.Frame(opt)
        r1.pack(fill="x")
        tk.Label(r1, text="Container:").pack(side="left")
        self.container_var = tk.StringVar(value=CONTAINERS["mkv"])
        cb = ttk.Combobox(
            r1,
            textvariable=self.container_var,
            values=list(CONTAINERS.values()),
            state="readonly",
            width=26,
        )
        return cb, opt, r1

    def _build_chapters(
        self, body: tk.Frame, cb: ttk.Combobox, opt: tk.LabelFrame, r1: tk.Frame
    ) -> None:
        cb.pack(side="left", padx=px(6))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._container_changed())
        self.chapters_var = tk.BooleanVar(value=True)
        tk.Checkbutton(r1, text="Keep chapters", variable=self.chapters_var).pack(
            side="left", padx=px(12)
        )
        tk.Label(r1, text="File title:").pack(side="left")
        self.file_title = tk.StringVar()
        tk.Entry(r1, textvariable=self.file_title, width=24).pack(side="left", padx=px(4))
        self.notes = tk.Label(
            opt, text="", fg=CLR["orange"], justify="left", anchor="w", wraplength=px(900)
        )
        self.notes.pack(fill="x", pady=px(6, 0))
        self.out_row = SaveRow(
            opt, t("common.output_file"), ext=".mkv", suggest=lambda: self._suggest()
        )
        self.out_row.pack(fill="x", pady=px(6, 0))
        self.btn = self.make_render_btn(body, "🗂  REMUX  (no re-encoding)", self._render, width=28)
        self.btn.pack(pady=px(8))
        self.console = self.make_console_block(body, height=5)

    # ── Files ─────────────────────────────────────────────────────────
    def load_file(self, path: str) -> bool:
        self.src_row.set(path)
        self._source_changed(path)
        return True

    def _source_changed(self, path: str) -> None:
        if (
            not path
            or not os.path.isfile(path)
            or (self.plan.inputs and self.plan.inputs[0] == path)
        ):
            return
        self._edit_index = None  # the editor's values belong to the old file
        try:
            self.plan.load(path)
        except ff.ProbeError as exc:
            self.log(self.console, f"Can't read {os.path.basename(path)}: {exc}")
            return
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        self.container_var.set(CONTAINERS.get("mp4" if ext == "m4v" else ext, CONTAINERS["mkv"]))
        self.out_row.set(self._suggest())
        self._refresh(select=0)

    def _container(self) -> str:
        return next((k for k, v in CONTAINERS.items() if v == self.container_var.get()), "mkv")

    def _suggest(self) -> str:
        src = self.plan.inputs[0] if self.plan.inputs else self.src_row.path
        return self.suggest_output(src, "remux", "." + self._container()) if src else ""

    def _container_changed(self) -> None:
        out = self.out_row.path
        if out:
            self.out_row.set(os.path.splitext(out)[0] + "." + self._container())
        self._update_notes()

    # ── Track list ────────────────────────────────────────────────────
    def _row_values(self, tr: remux.Track) -> tuple[str, ...]:
        flags = ", ".join(f for f, on in (("default", tr.default), ("forced", tr.forced)) if on)
        src = (
            os.path.basename(self.plan.inputs[tr.source])
            if tr.source < len(self.plan.inputs)
            else ""
        )
        if tr.delay:
            src += f" (+{tr.delay:g}s)"
        what = tr.codec.upper() if len(tr.codec) <= 5 else tr.codec
        return (
            "✓" if tr.keep else "✗",
            tr.kind.capitalize(),
            f"{what} · {tr.details}" if tr.details else what,
            lang_label(tr.language) if tr.language else "",
            tr.title,
            flags,
            src,
        )

    def _refresh(self, select: int | None = None) -> None:
        """Rebuild the list (after loading, moving or adding tracks) and select *select*."""
        sel = self._index() if select is None else select
        self._edit_index = None
        self.tree.delete(*self.tree.get_children())
        for i, tr in enumerate(self.plan.tracks):
            self.tree.insert("", "end", iid=str(i), values=self._row_values(tr))
        if sel is not None and 0 <= sel < len(self.plan.tracks):
            self.tree.selection_set(str(sel))
            self.tree.see(str(sel))
            self._selected()  # the editor follows now, not when the select event arrives
        self._update_notes()

    def _update_rows(self) -> None:
        """Show changed labels/flags without rebuilding the list or touching the selection."""
        for i, tr in enumerate(self.plan.tracks):
            if self.tree.exists(str(i)):
                self.tree.item(str(i), values=self._row_values(tr))
        self._update_notes()

    def _update_notes(self) -> None:
        probs = remux.problems(self.plan.tracks, self._container())
        self.notes.config(
            text="\n".join(f"⚠  Track {self.plan.tracks.index(tr) + 1}: {why}" for tr, why in probs)
        )

    def _index(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _selected(self) -> None:
        i = self._index()
        if i is None or i == self._edit_index:
            return
        self._apply_edit()  # an edit not yet saved belongs to the previous track
        tr = self.plan.tracks[i]
        self._editing = True
        self.lang_var.set(lang_label(tr.language) if tr.language else remux.LANGUAGE_NAMES[""])
        self.title_var.set(tr.title)
        self.default_var.set(tr.default)
        self.forced_var.set(tr.forced)
        self._editing = False
        self._edit_index = i

    def _apply_edit(self) -> None:
        """Save the editor's values into the track they were loaded from (not the selection: clicking
        another row moves the selection before the title box loses focus)."""
        i = self._edit_index
        if i is None or self._editing or i >= len(self.plan.tracks):
            return
        tr = self.plan.tracks[i]
        tr.language = lang_code(self.lang_var.get())
        tr.title = self.title_var.get().strip()
        if self.default_var.get() and not tr.default:
            self.plan.set_default(i)  # only one default per kind
        tr.default = bool(self.default_var.get())
        tr.forced = bool(self.forced_var.get())
        self._update_rows()

    def _toggle_keep(self) -> None:
        i = self._index()
        if i is not None:
            self.plan.tracks[i].keep = not self.plan.tracks[i].keep
            self._update_rows()

    def _move(self, step: int) -> None:
        i = self._index()
        if i is not None:
            self._apply_edit()
            self._refresh(select=self.plan.move(i, step))

    def _add_file(self) -> None:
        if not self.plan.inputs:
            messagebox.showwarning("Tracks", "Choose the main file first.", parent=self)
            return
        path = dialogs.ask_open("media", title="Audio or subtitle file to add", parent=self)
        if path:
            self.add_file(path)

    def add_file(self, path: str, delay: float = 0.0, language: str = "") -> None:
        self._apply_edit()
        try:
            added = self.plan.add_file(path, delay=delay, language=language)
        except (ValueError, ff.ProbeError) as exc:
            messagebox.showwarning("Tracks", str(exc), parent=self)
            return
        self.log(self.console, f"Added {len(added)} track(s) from {os.path.basename(path)}")
        self._refresh(select=len(self.plan.tracks) - 1)

    def _extract(self) -> None:
        i = self._index()
        if i is None:
            messagebox.showwarning("Tracks", "Select a track first.", parent=self)
            return
        tr = self.plan.tracks[i]
        src = self.plan.inputs[tr.source]
        base = os.path.splitext(src)[0]
        suggestion = (
            f"{base}.track{tr.index}"
            + (f".{tr.language}" if tr.language else "")
            + remux.extract_ext(tr)
        )
        out = dialogs.ask_save(
            suggestion, kind="media", defaultextension=remux.extract_ext(tr), parent=self
        )
        if not out:
            return
        self.extract(i, out)

    def extract(self, i: int, out: str) -> None:
        tr = self.plan.tracks[i]
        final = self.confirm_output(out, [self.plan.inputs[tr.source]])
        if not final:
            return
        cmd = remux.extract_command(self.plan.inputs[tr.source], tr, final)
        self.log(self.console, f"Saving {tr.describe()} → {os.path.basename(final)}")
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, final),
            btn=self.btn,
            output=final,
            inputs=list(self.plan.inputs),
            name="Save track",
        )

    # ── Remux ─────────────────────────────────────────────────────────
    def _render(self) -> None:
        if not self.plan.inputs:
            messagebox.showwarning(t("common.warning"), t("common.no_input"), parent=self)
            return
        out = self.out_row.path or self._suggest()
        if os.path.splitext(out)[1].lower().lstrip(".") != self._container():
            out = os.path.splitext(out)[0] + "." + self._container()
        out = self.confirm_output(out, list(self.plan.inputs))
        if not out:
            return
        self.out_row.set(out)
        title = self.file_title.get().strip() or None
        try:
            cmd, notes = remux.remux_command(
                self.plan.inputs,
                self.plan.tracks,
                out,
                keep_chapters=self.chapters_var.get(),
                title=title,
            )
        except ValueError as exc:
            messagebox.showwarning(t("common.warning"), str(exc), parent=self)
            return
        for n in notes:
            self.log(self.console, n)
        info = ff.try_probe(self.plan.inputs[0])
        self.run_ffmpeg(
            cmd,
            self.console,
            on_done=lambda rc: self.show_result(rc, out),
            btn=self.btn,
            duration=info.duration if info else None,
            output=out,
            inputs=list(self.plan.inputs),
            name="Remux",
        )
