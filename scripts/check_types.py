"""
scripts/check_types.py - the type-checking ratchet.

Runs pyright (settings in pyrightconfig.json) and compares the error count of
every file with tests/typing_baseline.json.  It fails when a file has MORE
errors than its baseline (or a new file has any), so typing can only improve.
When files have fewer errors it says so; `--update` writes the new, lower
baseline.  Robust Python ch. 6-7: adopt type checking gradually, but never
let it slide back.

    python scripts/check_types.py            # check (CI)
    python scripts/check_types.py --update   # after fixing errors
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "tests", "typing_baseline.json")


def current_errors() -> dict[str, int]:
    exe = shutil.which("pyright")
    if exe is None:
        sys.exit("pyright is not installed (pip install pyright)")
    res = subprocess.run(
        [exe, "--outputjson"], cwd=ROOT, capture_output=True, text=True, timeout=900
    )
    data = json.loads(res.stdout)
    counts: collections.Counter[str] = collections.Counter()
    for diag in data["generalDiagnostics"]:
        # missing third-party modules depend on what is installed where pyright runs
        if diag["severity"] == "error" and diag.get("rule") not in (
            "reportMissingImports",
            "reportMissingModuleSource",
        ):
            counts[os.path.relpath(diag["file"], ROOT).replace(os.sep, "/")] += 1
    return dict(sorted(counts.items()))


def main(argv: list[str]) -> int:
    now = current_errors()
    first_run = not os.path.exists(BASELINE)
    base: dict[str, int] = {}
    if not first_run:
        with open(BASELINE, encoding="utf-8") as fh:
            base = json.load(fh)
    worse = {f: (base.get(f, 0), n) for f, n in now.items() if n > base.get(f, 0)}
    better = {f: (n, now.get(f, 0)) for f, n in base.items() if now.get(f, 0) < n}
    print(
        f"pyright: {sum(now.values())} errors in {len(now)} files (baseline {sum(base.values())})"
    )
    for f, (was, is_) in sorted(worse.items()):
        print(f"  WORSE   {f}: {was} -> {is_}")
    for f, (was, is_) in sorted(better.items()):
        print(f"  better  {f}: {was} -> {is_}")
    if "--update" in argv:
        if worse and not first_run:
            print("Not updating: fix the files that got worse first.")
            return 1
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(now, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"baseline updated: {sum(now.values())} errors")
        return 0
    if better and not worse:
        print("Fewer errors than the baseline - run with --update to lock that in.")
    return 1 if worse else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
