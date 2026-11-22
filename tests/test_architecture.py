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
