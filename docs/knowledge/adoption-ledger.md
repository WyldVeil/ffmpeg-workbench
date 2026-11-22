# Adoption ledger

Every recommendation from the four book reviews (2026-11), and what became
of it. **Adopted** = done and, where possible, guarded by a test.
**Partly** = the pattern exists and new code follows it; older code moves
over as it is touched. **Deferred** = worth doing, not yet done (with the
reason). **Declined** = considered and rejected (with the reason).

The lint "debt" list in `pyproject.toml` and the allow-lists in
`tests/test_architecture.py` are the machine-readable side of this page:
they may only shrink.

## Robust Python

| # | Recommendation | Status | Where |
|---|---|---|---|
| R1 | Page Protocols instead of probing private attributes | Adopted | `tabs/kit/contracts.py`, `shell/shortcuts.py`; ruff SLF001 on |
| R2 | One composable range/moment picker widget | Deferred | the pages share `tabs/framestep.py` and the pickers in `tabs/widgets.py`; merging ~20 layouts is the next UI refactor |
| R3 | Enums + `ChoiceBox[E]` instead of labels in StringVars | Partly | `ChoiceBox` (`tabs/kit/widgets.py`), `StartPage`, `TaskStatus`, `ToolId`, `Category`; older drop-downs move over when touched |
| R4 | Narrow `Optional` once, at a blessed boundary | Adopted | `MediaInfo.require_video()/require_audio()`, `capabilities.peek()` |
| R5 | Pure ops below the UI | Adopted | `core/ops/`; `core/cli.py` imports no page (enforced) |
| R6 | Parameter objects instead of long keyword lists | Partly | read-once specs in Special Effects; 31 long signatures remain (ruff PLR0913 debt) |
| R7 | Type-checker adoption plan gated in CI | Adopted | `scripts/check_types.py` ratchet; strict list in `pyrightconfig.json` |
| R8 | Typed settings parsed once | Adopted | `core/settings.py` `Setting[T]`; `tests/test_settings_keys.py` |
| R9 | Immutable, typed probe results | Adopted | frozen `MediaInfo`, `Stream` |
| R10 | One tool registry | Adopted | `core/tools.py` + `tabs/registry.py`; `tests/test_tools.py` |
| R11 | BaseTab: documented contract, composition | Partly | contracts + defaults on `BaseTab`; it still carries UI helpers |
| R12 | Typed theme tokens and layout constants | Deferred | theme application moved out of core; palettes are still dicts |
| R13 | Context managers for processes and busy state | Partly | `proc.run` (always cleans up), `ui.hosted_by`; no `busy()` helper yet |
| R14 | Render job with invariants and an Enum status | Partly | `TaskStatus` StrEnum; the job is still a mutable class |
| R15 | An error-handling policy | Adopted | `core/errors.py`; CONVENTIONS.md; ruff BLE001/S110 on |
| R16 | Static analysis + architecture rules | Adopted | ruff rule set, pyright, `tests/test_architecture.py` |
| R17 | Typed thread hand-off | Adopted | `tasks.submit(fn, on_done=, on_error=) -> Future[T]` |
| R18 | Tests through public contracts | Partly | the harness opens pages through `show_page`; many tests still read page internals |
| R19 | Property-based and stateful tests | Adopted | `tests/test_properties.py` (the stateful test found a real bug: a broken settings file could overwrite the good backup) |
| R20 | Selective mutation testing | Deferred | worthwhile on `core/ops`; too slow on the GUI suite |
| R21 | Delete compatibility shims | Adopted | 75 `tabs/tab_*.py` shims and the base_tab re-exports removed |
| R22 | Visible module ownership | Adopted | module docstrings; ARCHITECTURE.md |
| R23 | Typed observers for app events | Declined | the only observable store (`core/state.py`) had no readers; deleted |
| R24 | Protocols instead of `object` escape hatches | Adopted | `Shell` protocol; `deps.require() -> ModuleType` |

## Effective Python

| # | Recommendation | Status | Where |
|---|---|---|---|
| R1 | Worker code never touches Tk | Adopted | fixed in Special Effects and the Video Downloader; CONVENTIONS.md |
| R2 | One bounded task service | Adopted | `core/tasks.py`; no other `threading.Thread` (enforced) |
| R3 | One subprocess gateway with a mandatory timeout | Adopted | `core/proc.py`; no other `subprocess` (enforced) |
| R4 | Stop swallowing exceptions | Adopted | the exception sweep; BLE001/S110 on |
| R5 | A root exception | Adopted | `AppError` |
| R6 | Results and exceptions, not `None` and magic numbers | Partly | `ProcResult`, `require_video()`; `ff.run` still returns a result object with a code |
| R7 | Typed settings object | Adopted | see Robust Python R8 |
| R8 | Tighter typing; `px()` misuse a type error | Adopted | `px()` overloads; strict files |
| R9 | Render job as a dataclass with an Enum status | Partly | see Robust Python R14 |
| R10 | Simpler queue worker loop | Partly | workers are `tasks.start_service` loops |
| R11 | Keyword-only options | Partly | all new APIs; older ones as touched |
| R12 | Fewer stateful closures and lambda chains | Partly | `functools.partial` in hand-offs; some pages still chain lambdas |
| R13 | Shared scaffolding for the tools | Partly | `BaseTab` defaults, `tabs/kit` |
| R14 | Dependency seams for tests | Adopted | `ui.hosted_by`, injectable settings file, capability cache folder |
| R15 | One logging stream | Adopted | module `logger`s under `crossfader.*` |
| R16 | Break up the App god class | Adopted | `shell/` |
| R17 | Output promotion as a context manager | Deferred | `.part` promotion works and is tested; a refactor without a bug to fix |
| R18 | Test conventions | Adopted | CONVENTIONS.md |
| R19 | Measure start-up and hot paths | Adopted | `devtools/perf.py`, `tests/test_perf.py` |
| R20 | Document thread ownership of shared state | Partly | "threads:" comments on the shared structures of the new modules; ARCHITECTURE.md |

## Fluent Python

| # | Recommendation | Status | Where |
|---|---|---|---|
| R1 | Capability Protocols | Adopted | see Robust Python R1 |
| R2 | Enums for choice sets + a choice widget | Partly | see Robust Python R3 |
| R3 | Immutable media values | Adopted | frozen probe results |
| R4 | Typed settings model | Adopted | `Setting[T]` |
| R5 | `ToolSpec` registry with stable ids | Adopted | `core/tools.py` |
| R6 | Queue job model | Partly | `TaskStatus` |
| R7 | Typed thread helper on futures | Adopted | `core/tasks.py` |
| R8 | `NamedTuple` for ad-hoc tuples | Partly | new code uses dataclasses; old tuples remain in places |
| R9 | Gradual typing, strict at the core | Adopted | `pyrightconfig.json` strict list |
| R10 | Library caches instead of hand-rolled ones | Partly | three hand-rolled capability caches replaced by `core/capabilities.py` (on disk, which `functools.cache` can't do) |
| R11 | `partial`, late binding in callbacks | Adopted | hand-offs use `partial`; ruff B023 on |
| R12 | A time value type with `__format__` | Adopted | `core/timecode.py` |
| R13 | `__repr__` / `__eq__` on boundary objects | Adopted | dataclasses throughout the new code |
| R14 | Fail fast at boundaries; narrow excepts | Adopted | see Effective Python R4 |
| R15 | Typed, weakly-held observable state | Declined | see Robust Python R23 |
| R16 | `singledispatch` | Declined | no type-switching code left that it would simplify |
| R17 | Mutable defaults and aliasing | Adopted | RUF012 on; frozen values; settings hand out copies |

## High Performance Python

| # | Recommendation | Status | Where |
|---|---|---|---|
| R1 | Capability probes in the background, cached on disk | Adopted | `core/capabilities.py` |
| R2 | The yt-dlp version check off the UI thread | Adopted | `tabs/social/youtube.py` |
| R3 | No program ever starts on the UI thread | Adopted | `tests/test_perf.py`, empty allow-list |
| R4 | Import edge-tts lazily | Adopted | `core/tts.edge_available()` uses `find_spec` |
| R5 | 8-bit preview frames | Adopted | `grab_frame_png` adds `format=rgb24` |
| R6 | Build notebook sub-pages on first view | Deferred | 40-60 ms per page open; behind the remaining items |
| R7 | Debounce the recent-tools settings write | Adopted | `shell/app.py` |
| R8 | Adaptive UI-queue polling | Adopted | `core/ui.py` backs off to 100 ms when idle |
| R9 | Faster scrubbing (a long-lived decoder) | Deferred | only if people ask: high effort |
| §4 | Permanent performance checks | Adopted | `tests/test_perf.py`; `devtools/perf.py` (stall watchdog, sampler, `timed`) |
