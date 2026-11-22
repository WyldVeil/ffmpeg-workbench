"""Architecture rules, checked on the source (Robust Python ch. 16 and 20).

Each rule lists the code that still breaks it.  The lists may only shrink: the
test fails when something new breaks a rule, and also when a listed exception
has been fixed but is still listed - remove it, so it can't come back.

1. ENGINE: core/ is the engine.  It never imports the GUI (tkinter, the tool
   pages in tabs/, main).
2. PROCESSES: only core's process modules start processes (subprocess).
3. THREADS: background work goes through the task service, not threading.Thread.
4. LAYERS: core <- tabs <- shell.  Tool pages (tabs/) never import the window
   (shell/, main): they reach it through the Shell protocol in tabs/kit.
"""

from __future__ import annotations

import ast
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_MODULES = ("tkinter", "tkinterdnd2", "tabs", "main", "shell")


def _py_files(*folders: str):
    if "main.py" in folders:
        yield "main.py", os.path.join(ROOT, "main.py")
    for folder in folders:
        if folder.endswith(".py"):
            continue
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, folder)):
            if "__pycache__" in dirpath:
                continue
            for name in sorted(files):
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    yield os.path.relpath(path, ROOT).replace(os.sep, "/"), path


def _imports(path: str) -> set[str]:
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _top(module: str) -> str:
    return module.split(".")[0]


# ── the exceptions still to fix (may only shrink) ────────────────────────────

ENGINE_EXCEPTIONS: dict[str, set[str]] = {}
PROCESS_EXCEPTIONS: set[str] = set()  # every program starts through core.proc
THREAD_EXCEPTIONS: set[str] = set()  # every thread starts through core.tasks


def _engine_breaks() -> dict[str, set[str]]:
    breaks: dict[str, set[str]] = {}
    for rel, path in _py_files("core"):
        bad = {_top(m) for m in _imports(path) if _top(m) in GUI_MODULES}
        if bad:
            breaks[rel] = bad
    return breaks


def _users(module_attr: tuple[str, str], *, allowed: tuple[str, ...]) -> set[str]:
    mod, attr = module_attr
    users: set[str] = set()
    for rel, path in _py_files("main.py", "core", "tabs", "shell"):
        if rel in allowed:
            continue
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src, path)
        for node in ast.walk(tree):
            hit = False
            if attr == "*":
                if (isinstance(node, ast.Import) and any(a.name == mod for a in node.names)) or (
                    isinstance(node, ast.ImportFrom) and node.module == mod
                ):
                    hit = True
            elif (
                isinstance(node, ast.Attribute)
                and node.attr == attr
                and isinstance(node.value, ast.Name)
                and node.value.id == mod
            ) or (
                isinstance(node, ast.ImportFrom)
                and node.module == mod
                and any(a.name == attr for a in node.names)
            ):
                hit = True
            if hit:
                users.add(rel)
                break
    return users


def test_engine_does_not_import_the_gui():
    breaks = _engine_breaks()
    new = {f: m for f, m in breaks.items() if not m <= ENGINE_EXCEPTIONS.get(f, set())}
    assert not new, f"core/ must not import the GUI: {new}"
    fixed = {f for f in ENGINE_EXCEPTIONS if f not in breaks}
    assert not fixed, f"fixed - remove from ENGINE_EXCEPTIONS: {sorted(fixed)}"


def test_processes_start_only_in_core_proc():
    users = _users(("subprocess", "*"), allowed=("core/proc.py",))
    assert users <= PROCESS_EXCEPTIONS, (
        f"use core.proc, not subprocess: {sorted(users - PROCESS_EXCEPTIONS)}"
    )
    fixed = PROCESS_EXCEPTIONS - users
    assert not fixed, f"fixed - remove from PROCESS_EXCEPTIONS: {sorted(fixed)}"


def test_threads_start_only_in_core_tasks():
    users = _users(("threading", "Thread"), allowed=("core/tasks.py",))
    assert users <= THREAD_EXCEPTIONS, (
        f"use core.tasks, not threading.Thread: {sorted(users - THREAD_EXCEPTIONS)}"
    )
    fixed = THREAD_EXCEPTIONS - users
    assert not fixed, f"fixed - remove from THREAD_EXCEPTIONS: {sorted(fixed)}"


def test_thread_pools_live_only_in_core_tasks():
    """A ThreadPoolExecutor elsewhere would start threads past the task service."""
    users = sorted(
        rel
        for rel, path in [*_py_files("core"), *_py_files("shell"), *_py_files("tabs")]
        if rel != "core/tasks.py" and "ThreadPoolExecutor" in open(path, encoding="utf-8").read()
    )
    assert not users, f"use core.tasks.submit / run_all: {users}"


#: pages that read a time field themselves, and why a time picker doesn't fit
OWN_TIME_FIELDS = {
    "tabs/cutting/trimmer.py": "the trimming tool itself: its own handles, set-to-preview buttons, fix-up on Enter",
    "tabs/audio/waveform.py": "the waveform editor's own in/out marks",
    "tabs/audio/replacer.py": "a signed offset (+/-), not a moment in a video",
    "tabs/social/chapter_markers.py": "a 'jump to' box for the chapter list",
}


def test_pages_read_times_through_the_kit():
    """A moment or a part of a video is chosen with tabs/kit/time_fields.py
    (MomentPicker / RangePicker), which parses, checks and offers 🎞 / ◉ - not a
    hand-made entry parsed on the spot."""
    import re

    pattern = re.compile(r"parse_time\(self\.\w+\.get\(\)")
    users = {
        rel
        for rel, path in _py_files("tabs")
        if not rel.startswith("tabs/kit/") and pattern.search(open(path, encoding="utf-8").read())
    }
    assert users <= set(OWN_TIME_FIELDS), (
        f"use MomentPicker / RangePicker: {sorted(users - set(OWN_TIME_FIELDS))}"
    )
    assert set(OWN_TIME_FIELDS) <= users, (
        f"fixed - remove from OWN_TIME_FIELDS: {sorted(set(OWN_TIME_FIELDS) - users)}"
    )


def test_tool_pages_never_import_the_window():
    breaks = {
        rel: bad
        for rel, path in _py_files("tabs")
        if (bad := {_top(m) for m in _imports(path) if _top(m) in ("shell", "main")})
    }
    assert not breaks, f"tabs/ must not import the window (use tabs.kit.contracts.Shell): {breaks}"


def test_no_hidden_imports():
    """``__import__("subprocess")`` would slip past every rule above."""
    hits = [
        f"{rel}:{node.lineno}"
        for rel, path in _py_files("main.py", "core", "tabs", "shell")
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read(), path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "__import__"
    ]
    assert not hits, f"import modules normally (or with importlib, for lazy tool pages): {hits}"


#: What the pages hand to core instead of spelling out (Robust Python ch. 2:
#: the engine owns the vocabulary).  FFmpeg options, and the prefixes of the
#: per-stream ones (-c:a:0, -metadata:s:s:1, ...); yt-dlp's long options.
FFMPEG_OPTIONS = frozenset(
    [
        "-/filter_complex",
        "-ac",
        "-acodec",
        "-af",
        "-an",
        "-analyzeduration",
        "-ar",
        "-autoexit",
        "-avoid_negative_ts",
        "-bf",
        "-bsf:v",
        "-bufsize",
        "-c",
        "-codecs",
        "-compression_level",
        "-copyts",
        "-cq",
        "-crf",
        "-deadline",
        "-dn",
        "-draw_mouse",
        "-f",
        "-fflags",
        "-filter_complex",
        "-filters",
        "-fps_mode",
        "-framerate",
        "-g",
        "-hide_banner",
        "-hwaccel",
        "-hwaccel_output_format",
        "-i",
        "-ignore_loop",
        "-init_hw_device",
        "-itsoffset",
        "-level",
        "-loglevel",
        "-loop",
        "-map",
        "-map_chapters",
        "-map_metadata",
        "-maxrate",
        "-movflags",
        "-nodisp",
        "-nostats",
        "-nostdin",
        "-pass",
        "-passlogfile",
        "-pix_fmt",
        "-preset",
        "-print_format",
        "-probesize",
        "-progress",
        "-qp",
        "-r",
        "-read_intervals",
        "-reset_timestamps",
        "-s",
        "-sample_fmt",
        "-segment_format_options",
        "-segment_times",
        "-select_streams",
        "-shortest",
        "-show_entries",
        "-show_format",
        "-show_streams",
        "-skip_frame",
        "-sn",
        "-ss",
        "-sseof",
        "-start_number",
        "-stream_loop",
        "-t",
        "-thread_queue_size",
        "-timecode",
        "-to",
        "-tune",
        "-update",
        "-v",
        "-vaapi_device",
        "-vcodec",
        "-vf",
        "-vframes",
        "-video_size",
        "-vn",
        "-window_title",
        "-y",
    ]
)
FFMPEG_OPTION_PREFIXES = (
    "-c:",
    "-b:",
    "-q:",
    "-filter:",
    "-frames:",
    "-profile:",
    "-tag:",
    "-metadata",
    "-disposition",
)
YTDLP_OPTIONS = frozenset(
    [
        "-o",
        "-x",
        "-g",
        "--add-metadata",
        "--audio-format",
        "--audio-quality",
        "--cookies",
        "--download-sections",
        "--downloader",
        "--downloader-args",
        "--embed-chapters",
        "--embed-metadata",
        "--embed-subs",
        "--embed-thumbnail",
        "--force-keyframes-at-cuts",
        "--get-title",
        "--live-from-start",
        "--merge-output-format",
        "--no-overwrites",
        "--no-playlist",
        "--password",
        "--proxy",
        "--rate-limit",
        "--skip-download",
        "--sponsorblock-mark",
        "--sponsorblock-remove",
        "--sub-format",
        "--sub-lang",
        "--username",
        "--write-auto-sub",
        "--write-sub",
        "--yes-playlist",
    ]
)
BINARY_NAMES = frozenset({"ffmpeg_path", "ffprobe_path", "ffplay_path", "get_binary_path"})


def _filter_syntax() -> re.Pattern[str]:
    with open(os.path.join(ROOT, "tests", "ffmpeg_filters.txt"), encoding="utf-8") as fh:
        names = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    return re.compile(r"(?<![\w.])(" + "|".join(sorted(names, key=len, reverse=True)) + r")=")


def _docstrings(tree: ast.AST) -> set[int]:
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def _command_text(text: str, filters: re.Pattern[str]) -> bool:
    return (
        text in FFMPEG_OPTIONS
        or text.startswith(FFMPEG_OPTION_PREFIXES)
        or text in YTDLP_OPTIONS
        or bool(filters.search(text))
    )


def _command_parts(tree: ast.AST, filters: re.Pattern[str]) -> list[tuple[int, str]]:
    docs = _docstrings(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if _command_text(node.value, filters):
                found.append((node.lineno, node.value[:40]))
        elif isinstance(node, ast.Attribute) and node.attr in BINARY_NAMES:
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.Name) and node.id in BINARY_NAMES:
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.ImportFrom) and any(a.name in BINARY_NAMES for a in node.names):
            found.append((node.lineno, "import of a binary's path"))
    return found


def test_pages_never_build_commands():
    """FFmpeg and yt-dlp commands are built in core (core/ops, core/ffmpeg...):
    no options, no filter syntax and no binary paths in tabs/ or shell/.  The
    pages read their fields and call the engine."""
    filters = _filter_syntax()
    hits = [
        f"{rel}:{line} {what!r}"
        for rel, path in _py_files("tabs", "shell")
        for line, what in _command_parts(
            ast.parse(open(path, encoding="utf-8").read(), path), filters
        )
    ]
    assert not hits, "build commands in core/ops and call them from the page:\n" + "\n".join(hits)


#: the folders whose modules are tool pages (plus tabs/all_in_one.py)
PAGE_FOLDERS = ("audio", "cutting", "social", "system", "transcoder", "visuals")


def _page_module(module: str) -> bool:
    parts = module.split(".")
    return module == "tabs.all_in_one" or (
        len(parts) >= 3 and parts[0] == "tabs" and parts[1] in PAGE_FOLDERS
    )


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found += [(node.lineno, node.module)] + [
                (node.lineno, f"{node.module}.{a.name}") for a in node.names
            ]
    return found


def test_pages_never_import_other_pages():
    """What two pages share lives in core or tabs/kit; a page handing work to
    another goes through tabs.kit.contracts (as Auto Subtitles -> Hard Subber)."""
    hits = []
    for rel, path in _py_files("tabs"):
        me = rel[:-3].replace("/", ".")
        if not _page_module(me):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        hits += [
            f"{rel}:{line} -> {mod}"
            for line, mod in _imported_modules(tree)
            if _page_module(mod) and mod != me
        ]
    assert not hits, f"move what's shared to core or tabs/kit: {hits}"


#: pages with a layout of their own instead of the standard header: the welcome screen's hero title
OWN_HEADER = {"tabs/system/start.py": "the welcome screen's hero title"}


def _calls(tree: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (ast.unparse(n.func) == name or ast.unparse(n.func).endswith("." + name))
        for n in ast.walk(tree)
    )


def _page_classes(tree: ast.AST) -> list[str]:
    return [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and any(ast.unparse(b) == "BaseTab" for b in n.bases)
    ]


def _title_labels(tree: ast.AST) -> list[int]:
    """tk.Label(text=... t("tab.<tool>") ...): a tool's title drawn by hand."""
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and ast.unparse(n.func) in ("tk.Label", "ttk.Label")
        and any(k.arg == "text" and "t('tab." in ast.unparse(k.value) for k in n.keywords)
    ]


def test_every_page_has_the_standard_header():
    """Titles, icons and subtitles go through tabs.base_tab.make_header - one
    look, one place to change it (there were 17 hand-built variants)."""
    missing, by_hand = [], []
    for rel, path in _py_files("tabs"):
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        if _page_classes(tree) and rel not in OWN_HEADER and not _calls(tree, "make_header"):
            missing.append(rel)
        by_hand += [f"{rel}:{line}" for line in _title_labels(tree)]
    assert not missing, f"call make_header(self, title, subtitle, icon=...): {missing}"
    assert not by_hand, f"a tool's title belongs in make_header: {by_hand}"


def _hand_built_scaffolding(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Assign)
            and ast.unparse(n.targets[0]) == "self.btn_render"
            and not (
                isinstance(n.value, ast.Call)
                and ast.unparse(n.value.func).endswith("make_render_btn")
            )
        ):
            found.append((n.lineno, "a render button not from make_render_btn"))
        if (
            isinstance(n, ast.Call)
            and ast.unparse(n.func) == "tk.Text"
            and any(k.arg == "bg" and "console_bg" in ast.unparse(k.value) for k in n.keywords)
        ):
            found.append((n.lineno, "a console Text (make_console_block)"))
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and ast.unparse(n.value.func).endswith("make_console")
            and isinstance(n.targets[0], ast.Tuple)
            and not ast.unparse(n.targets[0].elts[-1]).startswith("_")
        ):
            found.append((n.lineno, "a console and scrollbar packed by hand (make_console_block)"))
    return found


def test_pages_use_the_kit_for_buttons_and_consoles():
    """The main action is make_render_btn (one look); consoles are
    make_console_block (frame, text and scrollbar packed alike everywhere)."""
    hits = [
        f"{rel}:{line} {what}"
        for rel, path in _py_files("tabs")
        if rel != "tabs/base_tab.py"
        for line, what in _hand_built_scaffolding(
            ast.parse(open(path, encoding="utf-8").read(), path)
        )
    ]
    assert not hits, hits


def test_colours_are_picked_with_the_kit():
    """A colour choice is a tabs.kit.widgets.ColourButton (swatch, picker,
    readable text, '#RRGGBB'), not a button and askcolor wired by hand."""
    hits = [
        rel
        for rel, path in _py_files("tabs", "shell")
        if rel != "tabs/kit/widgets.py"
        and "colorchooser"
        in _imports(path)
        | {
            a.name
            for n in ast.walk(ast.parse(open(path, encoding="utf-8").read(), path))
            if isinstance(n, ast.ImportFrom) and n.module == "tkinter"
            for a in n.names
        }
    ]
    assert not hits, f"use ColourButton: {hits}"


def test_the_whole_engine_is_type_checked_strictly():
    """pyright's strict mode covers the engine, the window shell and the page kit;
    CI fails on any error, so this list may only grow."""
    with open(os.path.join(ROOT, "pyrightconfig.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    assert {"core", "shell", "tabs/kit"} <= set(config["strict"])


MAX_FUNCTION_LINES = 120


def test_no_function_is_longer_than_a_screen_or_two():
    """A function over 120 lines does too many things: split it into steps with
    names (ruff's PLR0915 also caps it at 50 statements)."""
    long: list[str] = []
    for rel, path in _py_files("main.py", "core", "shell", "tabs", "scripts", "devtools"):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = (node.end_lineno or node.lineno) - node.lineno + 1
                if lines > MAX_FUNCTION_LINES:
                    long.append(f"{rel}:{node.lineno} {node.name} ({lines} lines)")
    assert not long, "Split these into named steps:\n" + "\n".join(long)


def _busy_lambda(node: ast.Lambda) -> str | None:
    """Why *node* should be a named function, or None when it's a one-step callback."""
    body = node.body
    if (
        isinstance(body, (ast.Tuple, ast.List))
        and sum(isinstance(e, ast.Call) for e in body.elts) >= 2
    ):
        return "does several things"
    if isinstance(body, ast.BoolOp) and sum(isinstance(v, ast.Call) for v in body.values) >= 2:
        return "chains actions with and/or"
    if (
        isinstance(body, ast.IfExp)
        and isinstance(body.body, ast.Call)
        and isinstance(body.orelse, ast.Call)
    ):
        return "picks between actions"
    if (node.end_lineno or node.lineno) - node.lineno >= 3:
        return "is long"
    return None


def test_lambdas_do_one_thing():
    """A lambda is for one short step (``lambda _e: self._changed()``).  One that
    does several things, or runs over several lines, wants a name: a method,
    functools.partial, or ``after(0, fn, *args)``."""
    busy: list[str] = []
    for rel, path in _py_files("main.py", "core", "shell", "tabs"):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Lambda) and (why := _busy_lambda(node)):
                busy.append(f"{rel}:{node.lineno} {why}")
    assert not busy, "Give these lambdas a name:\n" + "\n".join(busy)


def _drop_down_vars(tree: ast.AST) -> set[str]:
    """Names of the variables shown by a drop-down (ttk.Combobox / OptionMenu)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = (
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        )
        if fn == "Combobox":
            found |= {_last_name(k.value) for k in node.keywords if k.arg == "textvariable"}
        elif fn == "OptionMenu" and len(node.args) > 1:
            found.add(_last_name(node.args[1]))
    return found - {""}


def _last_name(node: ast.expr) -> str:
    return (
        node.attr
        if isinstance(node, ast.Attribute)
        else node.id
        if isinstance(node, ast.Name)
        else ""
    )


def _reads_drop_down(node: ast.expr, shown: set[str]) -> bool:
    """*node* is ``var.get()`` (perhaps ``.strip()``/``.lower()``/``.split()[0]``) on a drop-down."""
    while isinstance(node, ast.Call | ast.Subscript):
        if isinstance(node, ast.Subscript):
            node = node.value
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            return False
        if f.attr == "get" and not node.args:
            return _last_name(f.value) in shown
        if f.attr not in ("strip", "lower", "upper", "split"):
            return False
        node = f.value
    return False


def _is_text(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    return isinstance(node, ast.Tuple | ast.List | ast.Set) and any(_is_text(e) for e in node.elts)


def _label_tests(tree: ast.AST) -> list[int]:
    """Lines comparing a drop-down's shown text with a literal (directly or via a local)."""
    shown = _drop_down_vars(tree)
    hits: list[int] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) or not shown:
            continue
        local = {
            t.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign) and _reads_drop_down(n.value, shown)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        for n in ast.walk(fn):
            if isinstance(n, ast.Compare) and len(n.comparators) == 1:
                pair = (n.left, n.comparators[0])
            elif (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("startswith", "endswith")
                and n.args
            ):
                pair = (n.func.value, n.args[0])
            else:
                continue
            for side, other in (pair, pair[::-1]):
                if _is_text(other) and (
                    _reads_drop_down(side, shown)
                    or (isinstance(side, ast.Name) and side.id in local)
                ):
                    hits.append(n.lineno)
                    break
    return sorted(set(hits))


def test_drop_downs_are_read_by_value_not_by_label():
    """A drop-down's text is for people (and translated): code compares what was
    chosen - a ChoiceBox's enum value, or a table looked up by the label - never
    the text itself (``var.get() == "Custom…"`` silently breaks in other languages)."""
    found = []
    for rel, path in _py_files("tabs", "shell"):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found += [f"{rel}:{line}" for line in _label_tests(tree)]
    assert not found, "Use a ChoiceBox (tabs/kit/widgets.py) or a lookup table:\n" + "\n".join(
        found
    )


#: the only file that may name the GitHub account: where the repository lives
ACCOUNT_ALLOWED = {"core/__init__.py"}


def test_the_account_is_named_in_one_place():
    """1.2.6 renamed the app to FFmpeg Workbench: the account the repository
    lives under appears only in core/__init__.py (APP_URL, MAINTAINER)."""
    from core import APP_URL

    account = APP_URL.split("/")[3].lower()
    found = []
    for rel, path in _py_files("main.py", "core", "shell", "tabs", "scripts", "devtools"):
        with open(path, encoding="utf-8") as fh:
            if account in fh.read().lower() and rel not in ACCOUNT_ALLOWED:
                found.append(rel)
    assert not found, found
