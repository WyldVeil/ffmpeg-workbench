"""Put every English string the code falls back on into locale/en_US.json.

  python scripts/harvest_strings.py [--check]

``t_or("key", "English")`` and ``Labels("prefix", {"code": "English"})`` carry
their English in the code, so the page works before a translation exists.
This copies those strings into en_US.json (the list translators work from)
when they aren't there yet.  --check only reports what's missing (exit 1)."""

from __future__ import annotations

import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ("core", "tabs", "shell")


def found() -> dict[str, str]:
    out: dict[str, str] = {}
    for d in DIRS:
        for dp, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(dp, f), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    args = node.args
                    if (
                        name == "t_or"
                        and len(args) == 2
                        and all(
                            isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args
                        )
                    ):
                        out.setdefault(args[0].value, args[1].value)  # type: ignore[attr-defined]
                    if (
                        name == "Labels"
                        and len(args) == 2
                        and isinstance(args[0], ast.Constant)
                        and isinstance(args[1], ast.Dict)
                    ):
                        prefix = args[0].value
                        for k, v in zip(args[1].keys, args[1].values, strict=True):
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                                out.setdefault(f"{prefix}.{k.value}", str(v.value))
    return out


def made() -> dict[str, str]:
    """The English of every Labels the modules make when imported (also those
    built from tables in core/, which the source alone doesn't show)."""
    import importlib
    import tempfile

    tmp = tempfile.mkdtemp()  # importing the app must not create its folders in the user's home
    for var in ("CONFIG", "DATA", "CACHE", "LOG"):
        os.environ.setdefault(f"CROSSFADER_{var}_DIR", tmp)
    sys.path.insert(0, ROOT)
    for d in DIRS:
        for dp, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(dp, f), ROOT)[:-3].replace(os.sep, ".")
                    try:
                        importlib.import_module(rel.removesuffix(".__init__"))
                    except Exception:  # noqa: BLE001 - a module that can't load here (another OS)
                        continue
    from core.i18n import ALL_LABELS

    return {f"{lb.prefix}.{code}": text for lb in ALL_LABELS for code, text in lb.english.items()}


def main() -> int:
    path = os.path.join(ROOT, "locale", "en_US.json")
    with open(path, encoding="utf-8") as fh:
        en = json.load(fh)
    missing = {k: v for k, v in {**made(), **found()}.items() if k not in en}
    if "--check" in sys.argv:
        for k, v in missing.items():
            print(f"missing from en_US.json: {k} = {v!r}")
        return 1 if missing else 0
    en.update(missing)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(en, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"added {len(missing)} strings to en_US.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
