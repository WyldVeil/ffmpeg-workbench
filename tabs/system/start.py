"""
tabs/system/start.py  -  Start page: "What do you want to do?"

The page the app opens on (configurable).  Three ways in:

* goals - everyday tasks ("Make a file smaller", "Fix the sound"...), each
  showing the few tools that do it, in plain words;
* search - type anything ("vertical", "too big", "hiss") and matching tools
  appear;
* a file - drop one in (or choose it) and get suggestions for that file:
  "It's 1.3 GB - shrink it", "It contains a GPS location - remove it"...
  Opening a suggestion opens the tool with the file already loaded.

Plus recently used tools and files, and a link to Home (every tool).
"""
from __future__ import annotations

import os
import tkinter as tk

from core import catalog
from core import settings as _settings
from core.scaling import px
from tabs.base_tab import CLR, UI_FONT, BaseTab
from tabs.widgets import scroll_body

MAX_WIDTH = 1080
PLACEHOLDER = "Search - e.g. too big, vertical, subtitles, background noise, slow motion"


def _mix(a: str, b: str, amount: float) -> str:
    """Blend colour *a* towards *b* (hex colours)."""
    try:
        ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return a
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(ca, cb))


def nav_key(name: str) -> str:
    """The shell's page name for tool *name* (the pinned builder has a prefix)."""
    return "⚡ " + name if name == catalog.AIO else name


class StartTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._navigate = None
        self._file: tuple | None = None        # (path, MediaInfo) to suggest tools for
        self._goal: str | None = None
        self._cols = 0
        self._search_job = None
        self._hover = _mix(CLR["panel"], CLR["accent"], 0.14)
        self._build_ui()

    # ── Hooks used by main.py ─────────────────────────────────────────────
    def set_navigator(self, fn):
        self._navigate = fn

    def on_show(self):
        """Called each time the page is shown: recent lists may have changed."""
        self._render_recent()

    # ── Layout ────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = scroll_body(self)
        self._col = tk.Frame(outer, bg=CLR["bg"])
        self._col.pack(fill="x", padx=px(32), pady=px(26, 30))
        # Centre on the visible width (the scroll canvas), not the inner frame:
        # the padding changes the frame's own width, so reacting to that fed
        # back into itself and grew without end when the content was wider
        # than MAX_WIDTH (HiDPI screens), until X refused the window.
        outer.master.bind("<Configure>", self._on_width, add=True)

        tk.Label(self._col, text="START", font=(UI_FONT, 9, "bold"), bg=CLR["bg"], fg=CLR["accent"]).pack(anchor="w")
        tk.Label(self._col, text="What do you want to do?", font=(UI_FONT, 24, "bold"), bg=CLR["bg"],
                 fg=CLR["fg"]).pack(anchor="w", pady=px(2, 2))
        tk.Label(self._col, text="Choose a goal, search, or drop in a file to see what you can do with it. "
                                 "Everything happens on this computer.",
                 font=(UI_FONT, 11), bg=CLR["bg"], fg=CLR["fgdim"], justify="left", anchor="w").pack(anchor="w")

        # Search
        box = tk.Frame(self._col, bg=CLR["input_bg"], highlightthickness=px(1), highlightbackground=CLR["border"],
                       highlightcolor=CLR["accent"])
        box.pack(fill="x", pady=px(18, 12))
        tk.Label(box, text="🔍", font=(UI_FONT, 13), bg=CLR["input_bg"], fg=CLR["fgdim"]).pack(side="left",
                                                                                              padx=px(12, 4))
        self._search_var = tk.StringVar()
        self._search = tk.Entry(box, textvariable=self._search_var, font=(UI_FONT, 13), relief="flat", bd=0,
                                bg=CLR["input_bg"], fg=CLR["fgdim"], insertbackground=CLR["fg"])
        self._search.pack(side="left", fill="x", expand=True, ipady=px(9), padx=px(2, 10))
        self._search.insert(0, PLACEHOLDER)
        self._search.bind("<FocusIn>", self._search_focus)
        self._search.bind("<FocusOut>", self._search_blur)
        self._search.bind("<KeyRelease>", self._search_typed)
        self._search.bind("<Return>", self._search_enter)
        self._search.bind("<Escape>", lambda _e: self.clear_search())
        self._search.bind("<Down>", lambda _e: self._focus_first_result())

        # File drop area + recent
        row = tk.Frame(self._col, bg=CLR["bg"])
        row.pack(fill="x")
        self._drop = tk.Canvas(row, height=px(118), bg=CLR["bg"], highlightthickness=0, cursor="hand2")
        self._drop.pack(side="left", fill="x", expand=True)
        self._drop.bind("<Configure>", lambda _e: self._draw_drop())
        self._drop.bind("<Button-1>", lambda _e: self._choose_file())
        self._recent = tk.Frame(row, bg=CLR["bg"], width=px(300))
        self._recent.pack(side="left", fill="y", padx=px(18, 0))

        # Suggestions for the file, then goals / goal tools / search results
        self._suggest_frame = tk.Frame(self._col, bg=CLR["bg"])
        self._suggest_frame.pack(fill="x")
        self._results = tk.Frame(self._col, bg=CLR["bg"])
        self._results.pack(fill="x", pady=px(14, 0))

        foot = tk.Frame(self._col, bg=CLR["bg"])
        foot.pack(fill="x", pady=px(22, 0))
        total = len(catalog.TOOLS)
        home = tk.Label(foot, text=f"🏠  Browse all {total} tools on Home  ›", font=(UI_FONT, 10, "bold"),
                        bg=CLR["bg"], fg=CLR["accent"], cursor="hand2")
        home.pack(side="left")
        home.bind("<Button-1>", lambda _e: self._go("Home"))
        tk.Label(foot, text="Tip: drop files anywhere in the app. Ctrl+F searches the sidebar.",
                 font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fgdim"]).pack(side="right")

        self._render_recent()
        self._render_results()

    def _on_width(self, event):
        max_w, margin = px(MAX_WIDTH), px(24)
        pad = max(margin, (event.width - max_w) // 2)
        self._col.pack_configure(padx=pad)
        avail = min(event.width, max_w) - 2 * margin
        cols = max(1, min(4, avail // px(250)))
        if cols != self._cols:
            self._cols = cols
            if not self._query() and self._goal is None:
                self._render_results()

    # ── Navigation ────────────────────────────────────────────────────────
    def _go(self, page: str) -> None:
        if self._navigate:
            self._navigate(page)

    def open_tool(self, name: str) -> None:
        """Open tool *name*; with a file chosen, load it there too."""
        key = nav_key(name)
        self._go(key)
        if not self._file:
            return
        app = self.winfo_toplevel()
        page = getattr(app, "pages", {}).get(key)
        if page is not None and hasattr(page, "load_file"):
            try:
                page.load_file(self._file[0])
            except Exception:
                import logging
                logging.getLogger("crossfader.start").exception("load_file failed in %s", key)

    # ── Search ────────────────────────────────────────────────────────────
    def _query(self) -> str:
        text = self._search_var.get()
        return "" if text == PLACEHOLDER else text.strip()

    def _search_focus(self, _e=None):
        if self._search_var.get() == PLACEHOLDER:
            self._search.delete(0, "end")
            self._search.config(fg=CLR["fg"])

    def _search_blur(self, _e=None):
        if not self._search_var.get():
            self._search.insert(0, PLACEHOLDER)
            self._search.config(fg=CLR["fgdim"])

    def _search_typed(self, event=None):
        if event is not None and event.keysym in ("Return", "Escape", "Down", "Up", "Tab"):
            return
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(120, self._render_results)

    def _search_enter(self, _e=None):
        hits = catalog.search(self._query())
        if hits:
            self.open_tool(hits[0])

    def search(self, text: str) -> list:
        """Type *text* into the search box (used by tests); returns the results shown."""
        self._search_focus()
        self._search_var.set(text)
        self._render_results()
        return catalog.search(text) if text.strip() else []

    def clear_search(self):
        self._search_var.set("")
        self._search_blur()
        self._render_results()

    def _focus_first_result(self):
        rows = [w for w in self._results.winfo_children() if getattr(w, "_tool", None)]
        if rows:
            rows[0].focus_set()

    # ── Files ─────────────────────────────────────────────────────────────
    def _choose_file(self):
        from core import dialogs
        path = dialogs.ask_open("media", title="Choose a file", parent=self)
        if path:
            self.load_file(path)

    def load_file(self, path) -> bool:
        from core import ffmpeg as ff
        info = ff.try_probe(path)
        if info is None or not (info.has_video or info.has_audio):
            self._file = None
            self._draw_drop(error=f"Can't read {os.path.basename(path)} as video, audio or a picture.")
            self._render_suggestions()
            return True
        self._file = (path, info)
        self._draw_drop()
        self._render_suggestions()
        self.after_idle(self._render_recent)          # the shell adds it to the recent files
        return True

    def clear_file(self):
        self._file = None
        self._draw_drop()
        self._render_suggestions()

    def _draw_drop(self, error: str = ""):
        c = self._drop
        c.delete("all")
        w, h = max(c.winfo_width(), px(200)), int(c["height"])
        if self._file:
            path, info = self._file
            c.create_rectangle(px(1), px(1), w - px(2), h - px(2), outline=CLR["accent"], fill=CLR["panel"], width=px(1))
            kind = "🎞" if info.has_video else "🎵"
            c.create_text(px(26), h / 2, text=kind, font=(UI_FONT, 26), fill=CLR["fg"], anchor="w")
            c.create_text(px(82), h / 2 - px(16), text=os.path.basename(path), font=(UI_FONT, 12, "bold"),
                          fill=CLR["fg"], anchor="w", width=w - px(200))
            c.create_text(px(82), h / 2 + px(10), text=catalog.describe(info), font=(UI_FONT, 10), fill=CLR["fgdim"],
                          anchor="w", width=w - px(200))
            c.create_text(px(82), h / 2 + px(32), text="Click to choose another file", font=(UI_FONT, 9),
                          fill=CLR["fgdim"], anchor="w")
            clear = c.create_text(w - px(22), px(20), text="✕", font=(UI_FONT, 12), fill=CLR["fgdim"], anchor="e")
            c.tag_bind(clear, "<Button-1>", lambda _e: (self.clear_file(), "break")[1])
            return
        c.create_rectangle(px(2), px(2), w - px(3), h - px(3), outline=_mix(CLR["border"], CLR["fg"], 0.25),
                           dash=px(6, 4),
                           fill=CLR["bg"], width=px(2))
        c.create_text(w / 2, h / 2 - px(14), text="⬇  Drop a video, audio file or photo here",
                      font=(UI_FONT, 12, "bold"), fill=CLR["fg"])
        c.create_text(w / 2, h / 2 + px(12), text=error or "or click to choose one - you'll get suggestions for it",
                      font=(UI_FONT, 10), fill=CLR["red"] if error else CLR["fgdim"])

    def _render_suggestions(self):
        for w in self._suggest_frame.winfo_children():
            w.destroy()
        if not self._file:
            self._suggest_frame.configure(height=1)      # an emptied frame keeps its old size otherwise
            return
        path, info = self._file
        tips = catalog.suggest(info)
        self._section(self._suggest_frame, f"Suggestions for {os.path.basename(path)}")
        grid = tk.Frame(self._suggest_frame, bg=CLR["bg"])
        grid.pack(fill="x")
        for i, (name, reason) in enumerate(tips):
            self._tool_row(grid, name, reason).grid(row=i // 2, column=i % 2, sticky="nsew", padx=px(0, 10),
                                                    pady=px(4))
        for c in (0, 1):
            grid.grid_columnconfigure(c, weight=1, uniform="s")

    # ── Recent ────────────────────────────────────────────────────────────
    def _render_recent(self):
        for w in self._recent.winfo_children():
            w.destroy()
        names = {nav_key(n) for n in catalog.TOOLS}
        tools = [n for n in (_settings.get("recent_tools", []) or []) if n in names][:4]
        files = [p for p in (_settings.get("recent_files", []) or []) if isinstance(p, str) and os.path.isfile(p)][:3]
        if not tools and not files:
            tk.Label(self._recent, text="New here?", font=(UI_FONT, 10, "bold"), bg=CLR["bg"],
                     fg=CLR["fg"]).pack(anchor="w")
            for line in ("Pick a goal below, or type what you", "want in the search box. Nothing is",
                         "uploaded - your files stay yours."):
                tk.Label(self._recent, text=line, font=(UI_FONT, 9), bg=CLR["bg"], fg=CLR["fgdim"]).pack(anchor="w")
            return
        if tools:
            tk.Label(self._recent, text="Recently used", font=(UI_FONT, 10, "bold"), bg=CLR["bg"],
                     fg=CLR["fg"]).pack(anchor="w")
            for key in tools:
                name = key.replace("⚡ ", "")
                info = catalog.TOOLS.get(name)
                lbl = tk.Label(self._recent, text=f"{info.icon if info else '•'}  {name}", font=(UI_FONT, 10),
                               bg=CLR["bg"], fg=CLR["accent"], cursor="hand2")
                lbl.pack(anchor="w", pady=px(1))
                lbl.bind("<Button-1>", lambda _e, k=key: self._go(k))
        if files:
            tk.Label(self._recent, text="Recent files", font=(UI_FONT, 10, "bold"), bg=CLR["bg"],
                     fg=CLR["fg"]).pack(anchor="w", pady=px(8 if tools else 0, 0))
            for p in files:
                name = os.path.basename(p)
                lbl = tk.Label(self._recent, text="📄  " + (name if len(name) <= 34 else name[:31] + "…"),
                               font=(UI_FONT, 10), bg=CLR["bg"], fg=CLR["accent"], cursor="hand2")
                lbl.pack(anchor="w", pady=px(1))
                lbl.bind("<Button-1>", lambda _e, f=p: self.load_file(f))

    # ── Goals / results ───────────────────────────────────────────────────
    def show_goal(self, key: str | None):
        self._goal = key
        self._render_results()

    def _render_results(self):
        self._search_job = None
        for w in self._results.winfo_children():
            w.destroy()
        q = self._query()
        if q:
            hits = catalog.search(q)
            self._section(self._results, f"Tools for “{q}”" if hits else f"Nothing matches “{q}”")
            if not hits:
                tk.Label(self._results, text="Try other words - or pick a goal below, or browse every tool on Home.",
                         font=(UI_FONT, 10), bg=CLR["bg"], fg=CLR["fgdim"]).pack(anchor="w", pady=px(0, 6))
                self._goal_grid()
                return
            self._tool_list(hits)
            return
        g = catalog.goal(self._goal) if self._goal else None
        if g is not None:
            back = tk.Label(self._results, text="‹  All goals", font=(UI_FONT, 10, "bold"), bg=CLR["bg"],
                            fg=CLR["accent"], cursor="hand2")
            back.pack(anchor="w")
            back.bind("<Button-1>", lambda _e: self.show_goal(None))
            self._section(self._results, f"{g.icon}  {g.title}", sub=g.blurb)
            self._tool_list(g.tools)
            return
        self._section(self._results, "Goals")
        self._goal_grid()

    def _goal_grid(self):
        grid = tk.Frame(self._results, bg=CLR["bg"])
        grid.pack(fill="x")
        cols = self._cols or 3
        for i, g in enumerate(catalog.GOALS):
            self._goal_card(grid, g).grid(row=i // cols, column=i % cols, sticky="nsew", padx=px(0, 10), pady=px(5))
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1, uniform="g")

    def _tool_list(self, names):
        grid = tk.Frame(self._results, bg=CLR["bg"])
        grid.pack(fill="x")
        cols = 2 if (self._cols or 3) >= 2 else 1
        for i, name in enumerate(names):
            self._tool_row(grid, name).grid(row=i // cols, column=i % cols, sticky="nsew", padx=px(0, 10), pady=px(4))
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1, uniform="t")

    # ── Pieces ────────────────────────────────────────────────────────────
    def _section(self, parent, title: str, sub: str = ""):
        tk.Label(parent, text=title, font=(UI_FONT, 13, "bold"), bg=CLR["bg"], fg=CLR["fg"]).pack(
            anchor="w", pady=px(16, 2 if sub else 8))
        if sub:
            tk.Label(parent, text=sub, font=(UI_FONT, 10), bg=CLR["bg"], fg=CLR["fgdim"]).pack(anchor="w",
                                                                                             pady=px(0, 8))

    def _card(self, parent):
        return tk.Frame(parent, bg=CLR["panel"], highlightthickness=px(1), highlightbackground=CLR["border"],
                        cursor="hand2", takefocus=1, highlightcolor=CLR["accent"])

    def _wire(self, card, on_click):
        """Click, hover and keyboard behaviour for a card and everything in it."""
        widgets, todo = [], [card]
        while todo:
            w = todo.pop()
            widgets.append(w)
            todo.extend(w.winfo_children())

        def paint(bg, border):
            for w in widgets:
                try:
                    w.config(bg=bg)
                except tk.TclError:
                    pass
            card.config(highlightbackground=border)

        for w in widgets:
            w.bind("<Button-1>", lambda _e: on_click())
            w.bind("<Enter>", lambda _e: paint(self._hover, CLR["accent"]))
            w.bind("<Leave>", lambda _e: paint(CLR["panel"], CLR["border"]))
        card.bind("<Return>", lambda _e: on_click())
        card.bind("<space>", lambda _e: on_click())
        card.bind("<FocusIn>", lambda _e: paint(self._hover, CLR["accent"]))
        card.bind("<FocusOut>", lambda _e: paint(CLR["panel"], CLR["border"]))
        return card

    def _goal_card(self, parent, g: catalog.Goal):
        card = self._card(parent)
        card._goal = g.key
        tk.Label(card, text=g.icon, font=(UI_FONT, 22), bg=CLR["panel"], fg=CLR["fg"]).pack(side="left",
                                                                                           padx=px(14, 8), pady=px(12))
        text = tk.Frame(card, bg=CLR["panel"])
        text.pack(side="left", fill="both", expand=True, pady=px(10), padx=px(0, 10))
        tk.Label(text, text=g.title, font=(UI_FONT, 11, "bold"), bg=CLR["panel"], fg=CLR["fg"], anchor="w").pack(
            anchor="w")
        blurb = tk.Label(text, text=g.blurb, font=(UI_FONT, 9), bg=CLR["panel"], fg=CLR["fgdim"], anchor="w",
                         justify="left", wraplength=px(190))
        blurb.pack(anchor="w", fill="x")
        self._wrap(text, blurb)
        return self._wire(card, lambda: self.show_goal(g.key))

    def _tool_row(self, parent, name: str, reason: str = ""):
        info = catalog.TOOLS.get(name, catalog.ToolInfo("•", ""))
        card = self._card(parent)
        card._tool = name
        tk.Label(card, text=info.icon, font=(UI_FONT, 17), bg=CLR["panel"], fg=CLR["fg"], width=2).pack(
            side="left", padx=px(12, 6), pady=px(8))
        tk.Label(card, text="Open ›", font=(UI_FONT, 9, "bold"), bg=CLR["panel"], fg=CLR["accent"]).pack(
            side="right", padx=px(12))
        text = tk.Frame(card, bg=CLR["panel"])
        text.pack(side="left", fill="both", expand=True, pady=px(8))
        tk.Label(text, text=name, font=(UI_FONT, 10, "bold"), bg=CLR["panel"], fg=CLR["fg"], anchor="w").pack(
            anchor="w")
        blurb = tk.Label(text, text=reason or info.blurb, font=(UI_FONT, 9), bg=CLR["panel"],
                         fg=CLR["orange"] if reason else CLR["fgdim"], anchor="w", justify="left", wraplength=px(360))
        blurb.pack(anchor="w", fill="x")
        self._wrap(text, blurb)
        return self._wire(card, lambda: self.open_tool(name))

    @staticmethod
    def _wrap(frame, label):
        """Wrap *label* to the width its card actually has."""
        def fit(event):
            width = max(px(120), event.width - px(6))
            if int(label.cget("wraplength")) != width:
                label.configure(wraplength=width)
        frame.bind("<Configure>", fit, add=True)
