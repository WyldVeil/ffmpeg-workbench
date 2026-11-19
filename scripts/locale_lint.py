#!/usr/bin/env python3
"""
scripts/locale_lint.py - Find (and optionally remove) broken translations.

A translation is rejected when it is provably wrong for its key:
  * separator debris from the old batch translator (``⟦``, ``§``, ``⟧``)
  * the value is a key name (``scaler.fps_method_mci``) or still a CLI token
  * ``{placeholders}`` differ from the English string
  * structural mismatch that betrays a value shifted onto the wrong key:
    different number of line breaks, an ellipsis/colon/question mark that
    the English text does or doesn't end with, or wildly different length

Removing a value simply makes the app fall back to English for that string,
which is always better than showing another string's text.

    python scripts/locale_lint.py            # report
    python scripts/locale_lint.py --fix      # remove offending values in place
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALE_DIR = os.path.join(ROOT, "locale")
CJK = {"zh_CN", "ja_JP", "ko_KR", "th_TH"}
# Locales damaged by the old misaligned batch translator: also drop values
# whose length matches a NEIGHBOURING key's English far better than their own.
SHIFTED = {"bn_BD", "fa_IR", "ms_MY", "ro_RO", "sw_KE", "tl_PH", "ur_PK"}

_ARTEFACT = re.compile(r"[⟦⟧§]")
_KEYLIKE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_.]+$")
_CLI = re.compile(r"^(?:[-+][A-Za-z][\w:]*|[a-z0-9_]+=\S*)$")
_PLACEHOLDER = re.compile(r"\{[a-zA-Z_0-9]*\}")


def _end_class(s: str) -> str:
    s = s.rstrip()
    if s.endswith(("…", "...")):
        return "ellipsis"
    if s.endswith((":", "：")):
        return "colon"
    if s.endswith(("?", "？", "؟")):
        return "question"
    return ""


def problems(key: str, en: str, tr: str, locale: str) -> list[str]:
    out = []
    if not isinstance(tr, str):
        return ["not a string"]
    if _ARTEFACT.search(tr) and not _ARTEFACT.search(en):
        out.append("separator debris")
    if _KEYLIKE.match(tr.strip()) and not _KEYLIKE.match(en.strip()):
        out.append("value is a key name")
    if _CLI.match(tr.strip()) and not _CLI.match(en.strip()):
        out.append("command syntax")
    if sorted(_PLACEHOLDER.findall(tr)) != sorted(_PLACEHOLDER.findall(en)):
        out.append("placeholder mismatch")
    if tr.count("\n") != en.count("\n") and en.count("\n") + tr.count("\n") > 0:
        out.append("line-break mismatch")
    if _end_class(en) != _end_class(tr) and (_end_class(en) in ("ellipsis", "question") or
                                              _end_class(tr) in ("ellipsis", "question")):
        out.append("ending mismatch")
    if locale not in CJK and len(en) >= 12:
        ratio = len(tr) / max(1, len(en))
        if ratio > 3.5 or ratio < 0.28:
            out.append(f"length ratio {ratio:.1f}")
    return out


def _neighbour_shifted(order, en, data) -> set:
    import math
    import statistics
    ratios = [len(data[k]) / len(en[k]) for k in order
              if k in data and isinstance(data[k], str) and len(en[k]) >= 8]
    if not ratios:
        return set()
    m = statistics.median(ratios)
    out = set()
    for i, k in enumerate(order):
        tr = data.get(k)
        if not isinstance(tr, str) or len(en[k]) < 8:
            continue
        ds = abs(math.log(max(len(tr), 1) / len(en[k]) / m))
        nbs = [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order) and len(en[order[j]]) >= 8]
        dn = min((abs(math.log(max(len(tr), 1) / len(en[n]) / m)) for n in nbs), default=9.0)
        if ds > math.log(2.2) and dn < math.log(1.35):
            out.add(k)
    return out


def lint(fix: bool = False, quiet: bool = False) -> int:
    with open(os.path.join(LOCALE_DIR, "en_US.json"), encoding="utf-8") as fh:
        en = json.load(fh)
    en.pop("_meta", None)
    total = 0
    for name in sorted(os.listdir(LOCALE_DIR)):
        if not name.endswith(".json") or name == "en_US.json":
            continue
        locale = name[:-5]
        path = os.path.join(LOCALE_DIR, name)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        bad = {}
        for key, tr in data.items():
            if key == "_meta" or key not in en:
                continue
            p = problems(key, en[key], tr, locale)
            if p:
                bad[key] = p
        if locale in SHIFTED:
            for k in _neighbour_shifted(list(en), en, data):
                bad.setdefault(k, ["neighbour-shifted"])
        stale = [k for k in data if k != "_meta" and k not in en]
        kinds = Counter(x.split(" ")[0] if x.startswith("length") else x for v in bad.values() for x in v)
        total += len(bad)
        if not quiet:
            print(f"{locale:7} {len(bad):5} bad, {len(stale):4} stale  {dict(kinds)}")
        if fix and (bad or stale):
            for k in list(bad) + stale:
                data.pop(k, None)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
    if not quiet:
        print(f"total: {total} offending translations" + (" removed" if fix else ""))
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()
    lint(fix=args.fix)
    sys.exit(0)
