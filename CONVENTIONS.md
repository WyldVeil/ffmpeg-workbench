# Conventions

The house rules, short. [ARCHITECTURE.md](ARCHITECTURE.md) explains the
structure they protect. **(enforced)** means a test or CI checks it.

## Python

* **Python 3.11+.** `from __future__ import annotations` at the top of every
  module. A module docstring says what belongs in the module.
* **Type every public function.** Prefer precise types: `ToolId` not `str`,
  `Timecode` not `float`, `Mapping` for read-only arguments, `Literal` /
  `StrEnum` for closed sets, frozen `@dataclass(slots=True)` for values.
  pyright must not get worse **(enforced)**; strict files stay at zero.
* **Values are immutable** unless they must change. `MediaInfo`, `Stream`,
  `ProcResult`, `ToolSpec`, `Timecode` and `Setting` are frozen.
* **Parse at the boundary.** Data from outside (settings.json, ffprobe JSON,
  a typed box) is converted once into typed values; past that point nothing
  re-checks it. Use `info.require_video()` rather than `info.video` when the
  file must have a picture.
* **Names**: `logger = logging.getLogger("crossfader.<area>")` per module;
  `_private` for module/class internals - other objects never read them
  **(enforced: SLF001)**; no `__dunder` mangling.
* **No compatibility shims** inside the codebase: when a name changes,
  change every caller (we own them all).

## Errors and logging

* Raise an `AppError` subclass (`core/errors.py`) for anything a person
  could cause or fix; always `raise New(...) from exc`.
* Catch the narrowest exception that can happen. A blind
  `except Exception` is allowed only at a boundary and must log with the
  traceback **(enforced: BLE001, S110)**.
* `contextlib.suppress(X)` for "this may fail and that's fine".
* Log for the person debugging, never for the person using the app. No
  personal paths or names in log lines that leave the machine.

## Threads and programs

* Start programs only with `core.proc` **(enforced)** - `proc.run(argv,
  timeout=...)` for a result, `proc.spawn(...)` for something you will
  stream or stop. Never `subprocess` directly, never `taskkill /IM`.
* Start background work only with `core.tasks` **(enforced)** -
  `tasks.submit(fn, on_done=..., on_error=...)`. `fn` must not touch a widget
  or a Tk variable: read them first, pass values in.
* Nothing that runs a program may run on the UI thread **(enforced)**. For
  "can this FFmpeg do X?" use `core.capabilities` (`peek` while building a
  page, `on_ready` to update it).
* Renders go through the queue: `self.run_ffmpeg(...)` or
  `self.enqueue_render(...)` - progress, cancel, atomic outputs for free.

## Settings

* Declare every setting once in `core/settings.py` (`Setting(key, default,
  choices)`), then use it as `settings.NAME.get()` / `.set(value)`. Reading
  an undeclared key fails a test **(enforced)**.
* Choices are `StrEnum`s; show them with `ChoiceBox` (never compare a
  widget's displayed text with a literal).

## Pages

* A tool is a `BaseTab` in `tabs/<section>/<tool>.py`, registered with one
  line in `tabs/registry.py` and one `ToolSpec` in `core/tools.py`
  **(enforced: tests/test_tools.py)**.
* Build FFmpeg commands in `core/ops/<tool>.py` as pure functions; the page
  maps widgets to arguments and calls them. The CLI uses the same functions.
* Offer abilities through the protocols in `tabs/kit/contracts.py`
  (`transport()`, `mark_in()`, `render()`, `load_file()`...). The window
  never looks at anything else.
* Sizes: every pixel value goes through `px()`; fonts are in points. Text:
  through `t()` (locale/), display only - never into an FFmpeg argument.
* Pages never import `shell/` or `main` **(enforced)**; use `self.shell()`.

## Tests

* `python -m pytest -q` (FFmpeg on PATH or in `bin/`). GUI tests drive the
  real window headlessly through `tests/gui_harness.py`.
* Test behaviour through public contracts; arrange-act-assert; parametrize
  tables of cases; Hypothesis for anything with a round trip, an invariant or
  a state machine.
* A bug fix comes with the test that would have caught it.

## Tooling

```sh
python -m ruff check main.py core shell tabs tests scripts devtools
python scripts/check_types.py            # pyright ratchet (--update after an improvement)
python -m pytest -q
```

Commits: one logical change each, imperative subject line, body says why.
