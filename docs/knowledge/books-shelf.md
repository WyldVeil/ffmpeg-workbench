# The books shelf

Four books shaped this codebase. Each was read for what it could change
*here*; [adoption-ledger.md](adoption-ledger.md) records every recommendation
and what became of it. This page says what each book is good for, so the
next person knows where to look.

## Robust Python - Patrick Viafore (O'Reilly, 2021)

The book about code that survives its future maintainers.

* **Types as communication** (ch. 2-7): annotate the "money-makers" first,
  adopt the checker gradually, then tighten module by module.
  → `scripts/check_types.py` ratchet; strict files in `pyrightconfig.json`.
* **Make illegal states unrepresentable** (ch. 4, 8-10): `Optional` once at a
  boundary, `Enum`/`Literal` for closed sets, frozen dataclasses for values,
  classes only for invariants.
  → `Timecode`, `ToolId`, `TaskStatus`, `StartPage`, frozen `MediaInfo`,
  `MediaInfo.require_video()`.
* **Protocols** (ch. 13): duck typing with a checked contract.
  → `tabs/kit/contracts.py`.
* **Parse at the boundary** (ch. 14). → `Setting.coerce`, `capabilities`.
* **Dependencies and layers** (ch. 16), **composition** (ch. 12), **pure
  policy vs mechanism** (ch. 17). → `core ← tabs ← shell`, `core/ops`.
* **The safety net** (ch. 20-24): linters, architecture rules, tests,
  property-based and stateful tests.
  → `tests/test_architecture.py`, `tests/test_properties.py`.

## Effective Python, 3rd ed. - Brett Slatkin (2024)

The book of concrete, itemised practice.

* **Subprocesses** (item 67: always a timeout). → `core/proc.py`.
* **Threads and pools** (items 68-74: don't share widgets across threads,
  bound the threads, futures carry exceptions). → `core/tasks.py`.
* **A root exception per API** (item 121). → `core/errors.py`.
* **Robustness and logging** (items 80-88: narrow excepts, log the rest).
  → the exception sweep; ruff BLE001/S110 on.
* **Testing** (items 108-113) and **profiling** (items 92-98).
  → Hypothesis tests; `devtools/perf.py`; `tests/test_perf.py`.
* **Composition over a god class** (items 29, 54). → `shell/`.

## Fluent Python, 2nd ed. - Luciano Ramalho (2022)

The book about the data model and writing *Pythonic* objects.

* **Data class builders** (ch. 5) and **object references** (ch. 6):
  frozen values that are safe to share. → probe cache returns frozen objects.
* **A Pythonic object** (ch. 11: `__format__`, `__repr__`, hashing,
  ordering). → `Timecode`.
* **Interfaces and protocols** (ch. 13), **type hints** (ch. 8, 15:
  generics, variance, overloads). → contracts; `ProcResult[AnyStr]`;
  `Setting[T]`; `ChoiceBox[E]`; `px()` overloads.
* **Futures** (ch. 20). → `tasks.submit` returns a `Future`.

## High Performance Python, 3rd ed. - Gorelick & Ozsvald (2025)

The book about measuring before optimising.

* **Profile to find the bottleneck** (ch. 2): the measured problems were all
  *latency on the UI thread* (one-off programs, a heavy import), not compute.
  → `core/capabilities.py` (probes off the UI thread, cached on disk);
  lazy edge-tts; 8-bit preview frames; adaptive UI polling; debounced
  settings writes.
* **Guard what you measured** (ch. 2): deterministic checks first, timing
  budgets as a backstop. → `tests/test_perf.py` (no program on the UI
  thread; no heavy imports at start-up; a start-up budget).
* What it says *not* to do here: optimise the already-fast paths (start-up
  0.18 s, page builds ~30 ms). Left alone.
