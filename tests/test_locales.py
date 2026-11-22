"""Locale hygiene: every translation must plausibly belong to its key, and
no FFmpeg syntax may live in the string catalogue."""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def test_no_corrupted_translations():
    from locale_lint import lint

    assert lint(fix=False, quiet=True) == 0, "run scripts/locale_lint.py to see the offenders"


def test_catalogue_contains_no_cli_syntax():
    with open(os.path.join(ROOT, "locale", "en_US.json"), encoding="utf-8") as fh:
        en = json.load(fh)
    cli = re.compile(r"^(?:[-+][a-z][\w:]*|-c:[av]\b.*|[a-z0-9_]+=[^\s]+)$")
    offenders = {k: v for k, v in en.items() if isinstance(v, str) and cli.match(v.strip())}
    assert not offenders, offenders


def test_t_is_not_used_inside_ffmpeg_argument_lists():
    """No t(...) call may appear inside a list that starts an ffmpeg argv."""
    pattern = re.compile(
        r'\[\s*(?:ffmpeg|ffprobe|ff_exe|self\._ffmpeg|get_binary_path\([^)]*\))[^\]]*?\bt\(\s*"([^"]+)"',
        re.S,
    )
    hits = []
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "tabs")):
        for f in files:
            if f.endswith(".py"):
                with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                    for m in pattern.finditer(fh.read()):
                        hits.append(f"{f}: t({m.group(1)!r})")
    assert not hits, hits


def test_fragments_are_valid_json_strings():
    folder = os.path.join(ROOT, "locale", "fragments")
    for name in os.listdir(folder) if os.path.isdir(folder) else ():
        if name.endswith(".json"):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                data = json.load(fh)
            assert all(isinstance(v, str) for v in data.values()), name


def test_no_text_is_just_another_key():
    """An entry whose "text" is another entry's key shows that key on screen
    (106 such aliases once did: the Settings page read "settings.default_crf_label")."""
    folder = os.path.join(ROOT, "locale")
    with open(os.path.join(folder, "en_GB.json"), encoding="utf-8") as fh:
        keys = set(json.load(fh))
    bad = []
    for name in sorted(os.listdir(folder)):
        if name.endswith(".json"):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                bad += [
                    f"{name}: {k}"
                    for k, v in json.load(fh).items()
                    if isinstance(v, str) and v in keys and v != k
                ]
    assert not bad, bad[:10]
