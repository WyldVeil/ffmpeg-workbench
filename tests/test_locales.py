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


def test_every_language_offered_has_its_file():
    """The language list and the files agree: each language offered has a file
    that says what it is, with no entry the English catalogue doesn't have."""
    from core.i18n import LANGUAGE_NAMES, SUPPORTED_LANGUAGES

    folder = os.path.join(ROOT, "locale")
    with open(os.path.join(folder, "en_US.json"), encoding="utf-8") as fh:
        en = {k for k, v in json.load(fh).items() if isinstance(v, str)}
    assert set(SUPPORTED_LANGUAGES) == set(LANGUAGE_NAMES)
    for code in SUPPORTED_LANGUAGES:
        with open(os.path.join(folder, f"{code}.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["_meta"]["code"] == code
        assert set(data) - {"_meta"} <= en, (code, sorted(set(data) - en - {"_meta"})[:5])


def test_greek_capitals_drop_accents_and_the_menu_keeps_them():
    """Greek is typeset without accents in capitals (the sidebar's headings),
    but the menu bar shows the words as written - title-casing the capitals
    back could not restore the accents.  Czech and Hungarian keep theirs."""
    from core import i18n
    from core.tools import Category
    from tabs.kit.labels import capitals, menu_section, section

    assert capitals("Ηχοληψία") == "ΗΧΟΛΗΨΙΑ"
    assert capitals("Střihovna") == "STŘIHOVNA" and capitals("Vágószoba") == "VÁGÓSZOBA"
    try:
        i18n.load_language("el_GR")
        assert section(Category.AUDIO) == "ΗΧΟΛΗΨΙΑ"
        assert menu_section(Category.AUDIO) == "Ηχοληψία"
        i18n.load_language("en_US")
        assert menu_section(Category.AUDIO) == "Audio Engineering"  # capitals, title-cased
    finally:
        i18n.load_language("en_US")


_TEXT_KW = {"text", "title", "label", "message", "header", "subtitle"}
_DIALOGS = {
    "showinfo",
    "showwarning",
    "showerror",
    "askyesno",
    "askokcancel",
    "askyesnocancel",
    "askretrycancel",
    "askquestion",
}
#: the app's helpers whose string arguments are on-screen labels
_LABEL_FNS = {
    "_spin", "form_row", "make_render_btn", "make_header", "_lbl", "number", "_effect_page",
    "FileRow", "add_tooltip", "_Section", "_slider_row", "_combo", "labelled", "radio", "num",
    "_lf", "section", "_stage_frame", "_labeled_entry", "hint", "FolderRow", "_btn", "_fx_num",
    "scale", "seconds",
}  # fmt: skip


def _words(s: str) -> bool:
    """Text a person reads: words, not a codec name, file pattern or colour."""
    v = s.strip()
    if not re.search(r"[A-Za-z]{2,}", v):
        return False
    if " " in v:
        return True
    return not (
        re.fullmatch(r"[*.#\-+/\\:%\w]*", v)
        and (not v[:1].isupper() or v.isupper() or any(c in v for c in "*.#_/\\%") or len(v) <= 3)
    )


def _shown_text(node):
    """The string arguments of call *node* that end up on screen."""
    import ast

    fn = node.func
    called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    values = [kw.value for kw in node.keywords if kw.arg in _TEXT_KW]
    if called in _DIALOGS:
        values += node.args[:2]
    if called in _LABEL_FNS:
        values += node.args
    if called in ("log", "log_tagged") and node.args:
        values.append(node.args[1] if len(node.args) > 1 else node.args[0])
    for v in values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            yield v.lineno, v.value
        elif isinstance(v, ast.JoinedStr):
            yield v.lineno, "".join(str(p.value) for p in v.values if isinstance(p, ast.Constant))


def test_on_screen_text_goes_through_the_catalogue():
    """Labels, titles, dialog messages and log lines come from locale/ (t()),
    so every language can show them; English typed straight into a page
    stays English for everyone."""
    import ast

    found = []
    for folder in ("tabs", "shell"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, folder)):
            for name in (f for f in files if f.endswith(".py")):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        for line, text in _shown_text(node):
                            if _words(text):
                                found.append(f"{os.path.relpath(path, ROOT)}:{line} {text[:40]!r}")
    assert not found, f"{len(found)} on-screen strings not in the catalogue: {found[:10]}"
