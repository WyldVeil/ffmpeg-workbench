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
