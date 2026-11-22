# Architecture

How FFmpeg Workbench is put together, and why. Read this before
changing anything structural; read [CONVENTIONS.md](CONVENTIONS.md) before
changing anything at all. Every rule marked **(enforced)** is checked by a
test or by CI, so it cannot quietly erode.

The design follows four books, chapter references inline:
*Robust Python* (Viafore), *Effective Python*, 3rd ed. (Slatkin),
*Fluent Python*, 2nd ed. (Ramalho) and *High Performance Python*, 3rd ed.
(Gorelick & Ozsvald). [docs/knowledge/](docs/knowledge/) records what was
taken from each and where it lives.

## 1. Three layers, one direction

```
            ┌──────────────────────────────────────────────┐
  shell/    │  the window: sidebar, pages, menus, status,  │   composition root
            │  shortcuts, files, versions                  │
            └──────────────────────┬───────────────────────┘
                                   │ imports
            ┌──────────────────────▼───────────────────────┐
  tabs/     │  81 tool pages + tabs/kit (contracts,        │   GUI
            │  ChoiceBox, theme, labels)                   │
            └──────────────────────┬───────────────────────┘
                                   │ imports
            ┌──────────────────────▼───────────────────────┐
  core/     │  the engine: FFmpeg, processes, tasks,       │   no Tk
            │  settings, tools, errors, ops (builders)     │
            └──────────────────────────────────────────────┘
  main.py   picks one: core/cli.py (no window) or shell/app.py
```

* **core/ never imports the GUI** (tkinter, tabs, shell, main) **(enforced:
  tests/test_architecture.py)**. A handful of core modules that predate the
  rule still import tkinter lazily to show a dialog (dialogs, logs, outputs); they
  are listed in the test and the list may only shrink.
* **Tool pages never import the window** **(enforced)**. A page reaches the
  window only through the `Shell` protocol (`tabs/kit/contracts.py`).
* **The command line is the engine plus argparse.** `core/cli.py` imports no
  page: the FFmpeg recipes it shares with the window live in `core/ops/`.

Dependencies point downwards only, so the bottom layer is the most stable
and the most tested (Robust Python ch. 16: high fan-in at the bottom).

## 2. The engine (`core/`)

| Module | What it owns |
|---|---|
| `errors.py` | One exception root, `AppError`: an expected failure whose message is fit to show. Anything else is a bug. (Effective Python item 121) |
| `proc.py` | **The only place a program starts** **(enforced)**. `spawn()` / `run(..., timeout=...)` (the timeout has no default on purpose), cancelling, process groups, `kill_all()` at exit, a frozen `ProcResult`. (Effective Python item 67) |
| `tasks.py` | **The only place a thread starts** **(enforced)**. A bounded `ThreadPoolExecutor`; `submit(fn, on_done=, on_error=)` delivers on the UI thread; unhandled errors are logged, never lost. `start_service()` for the few long-lived loops. (Effective Python items 72-74; Fluent Python ch. 20) |
| `ui.py` | The bridge from any thread to the Tk thread: `call_soon()`. |
| `capabilities.py` | Facts about the installed FFmpeg (filters, Vulkan, anlmdn) learned once **off the UI thread** and cached on disk keyed by the binary's identity. `get` / `peek` / `on_ready`. |
| `settings.py` | Every setting declared once as `Setting[T]` with its type and choices; values checked at the boundary; atomic writes with a backup that is never a broken file. (Robust Python ch. 14) |
| `tools.py` | `ToolId` (stable ids, what settings store) and `ToolSpec` (name, section, words). |
| `timecode.py` | `Timecode`, a value type: milliseconds, `__format__`, ordering, frame snapping. (Fluent Python ch. 11) |
| `ffmpeg.py` | Probing (frozen `MediaInfo` / `Stream`, cached), running with progress, frame grabs. |
| `ops/` | Pure command builders, one module per tool family: values in, FFmpeg arguments out. Shared by the pages and the CLI. |
| `queue_manager.py` | The render queue: jobs with a `TaskStatus` enum, progress, atomic `.part` outputs. |
| the rest | One concern each: `network` (proxies, certificates), `encoders` (GPU/CPU choice), `filters`, `whisper`, `tts`, `metaclean`, `catalog`... |

### Processes, threads and the UI thread

Tk is single-threaded, and the window must never wait on a program. So:

1. A page reads what it needs from its widgets **on the UI thread**, into
   plain values (ideally a frozen dataclass).
2. It hands a function of those values to `tasks.submit()`; the function
   may run programs through `proc`, but never touches a widget.
3. The result (or the error) comes back to `on_done` / `on_error` on the UI
   thread.

`tests/test_perf.py` starts a fresh app, opens every page, loads a file into
each, and fails if any program starts on the UI thread **(enforced; the
allow-list is empty)**. It also fails if start-up imports a heavy optional
library or takes longer than its budget. (High Performance Python ch. 2:
measure, then guard what you measured.)

Every test also fails if it leaves a program running (`tests/conftest.py`).

### Errors

* Expected failures raise an `AppError` subclass, chained with
  `raise ... from exc`. The window shows their message as it is.
* No `except Exception` swallows silently **(enforced: ruff BLE001, S110)**:
  each one either handles a named exception, logs with the traceback, or is a
  deliberate boundary (a batch job reporting one file's failure) that logs.
* Callbacks into page code (`on_line`, `on_progress`, `on_show`...) are
  boundaries: a bug in one is logged and the caller carries on.

## 3. The pages (`tabs/`)

One module per tool under `tabs/<section>/`, each a `BaseTab`.
`tabs/registry.py` maps each `ToolId` to a `LazyTab`: a page's module is
imported the first time it is opened, so start-up stays fast and a broken
tool shows an error page instead of stopping the app.

### Contracts, not attribute-poking

The window asks a page for abilities defined as `typing.Protocol`s in
`tabs/kit/contracts.py` (Fluent Python ch. 13, Robust Python ch. 13):

| Protocol | Used for |
|---|---|
| `HasTransport` → `Transport` | Home / End / ← / → / Space: a playhead (`VideoTimeline` is one; `ScrubTransport` adapts a slider) |
| `MarksIn`, `MarksOut` | the I and O keys |
| `Renderable` | Ctrl+S |
| `AcceptsFiles` | drag-and-drop, File › Open, Recent Files |
| `Previewing`, `ClosingAware`, `Showable`, `Navigating` | leaving a page, quitting, being shown, opening other tools |
| `Shell` | the other direction: what a page may ask of the window |

`BaseTab` gives sensible defaults (no playhead, press `btn_render`, stop the
preview player); a page overrides what it has. The shell checks with
`isinstance(page, MarksIn)` and calls the method - it never reads a private
name **(enforced: ruff SLF001)**.

### Typed widgets

`tabs/kit/widgets.py`: `ChoiceBox[E]` shows labels but its value is an enum
member, so code never compares against displayed (or translated) text.

## 4. The window (`shell/`)

`shell/app.py` is a composition root (Effective Python item 54): it builds
the parts and routes between them; each part is its own small class.

```
App ─┬─ Sidebar        tool list, sections, search            shell/sidebar.py
     ├─ PageHost       builds pages on first use, shows one    shell/pages.py
     ├─ StatusBar      status, render progress, queue          shell/statusbar.py
     ├─ MenuBar        built from core/tools                   shell/menus.py
     ├─ Shortcuts      keys → contracts                        shell/shortcuts.py
     ├─ FileOpener     open, recent, drag-and-drop             shell/files.py
     ├─ ToolVersions   FFmpeg / yt-dlp in use, updates         shell/versions.py
     └─ WheelRouter    the wheel scrolls what's under it       shell/wheel.py
```

## 5. Data flow of a render

```
page widgets ──(UI thread)──► values / spec ──► core/ops builder ──► argv
     │                                                                 │
     └── run_ffmpeg / enqueue_render ──► queue_manager (worker) ──► core.proc
                                              │ progress, status (TaskStatus)
                                              ▼
                               shell StatusBar  +  the page's button
```

Outputs are written to `name.part.ext` and renamed only on success, so a
failed or cancelled job never leaves a broken file. Inputs are never
overwritten.

## 6. Quality gates

| Gate | Where | Rule |
|---|---|---|
| ruff | CI | the book-recommended rule set with **no exceptions**: every function under complexity 15, 8 parameters and 50 statements; `ruff format` is the one style |
| pyright | CI (`scripts/check_types.py`) | **zero errors**: standard mode everywhere, **strict** for the foundation modules listed in `pyrightconfig.json`; the baseline (`tests/typing_baseline.json`) is empty, so any new error fails CI |
| architecture | `tests/test_architecture.py` | layers (no engine exceptions), process gateway, task service and its pools, pages read times through `tabs/kit/time_fields.py`, drop-downs read by value not label, no function over 120 lines, lambdas do one thing, no hidden imports |
| settings | `tests/test_settings_keys.py` | every key the code reads is declared |
| performance | `tests/test_perf.py` | no program on the UI thread; start-up budget; no heavy imports |
| properties | `tests/test_properties.py` | Hypothesis: Timecode round-trips and rounding, tool-id parsing, a stateful model of the settings store |
| behaviour | the rest of `tests/` | ~770 tests, most driving the real window headlessly (`tests/gui_harness.py`, Xvfb) with real FFmpeg on generated media |

## 7. Decisions, and what was deliberately not done

* **No asyncio.** The work is programs and files; threads plus a pool are
  the simplest correct tool, and Tk has its own loop.
* **No pydantic, no plug-in framework, no message bus.** One process, one
  JSON file, tools shipped together: typed dataclasses, a typed registry and
  plain callables give the same guarantees without the dependencies.
* **No ABCs for pages.** Protocols describe what the window needs without
  forcing an inheritance tree; `BaseTab` is convenience, not contract.
* **Pure builders first.** Anything that turns choices into an FFmpeg
  command belongs in `core/ops`, where it is testable without a display.
