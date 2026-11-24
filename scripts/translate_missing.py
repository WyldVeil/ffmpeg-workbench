"""
translate_missing.py  --  Fill missing locale keys, with a local model or Google Translate.

Usage:
    python scripts/translate_missing.py --local                # every language, local model
    python scripts/translate_missing.py --local --lang cy_GB   # one language
    python scripts/translate_missing.py                        # Google Translate (free endpoint)
    python scripts/translate_missing.py --dry-run              # what would be translated

--local uses any OpenAI-compatible server (default: KoboldCpp at
http://127.0.0.1:5001, e.g. a Gemma model started for translating).  Strings
go as a JSON object of {key: English} per batch and come back the same way.
Google gets one line per string; a batch that comes back with a different
number of lines is split in half and tried again, so no translation can land
on another string's key - the fault the old separator-based batches had.

A language without a file gets one.  Placeholders ({name}), line breaks and
a closing "…" or "?" are kept; a string that still doesn't fit is left out
(the app shows the English).  No packages needed.  Run
scripts/locale_lint.py afterwards.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

LOCALE_DIR = Path(__file__).parent.parent / "locale"
sys.path.insert(0, str(Path(__file__).parent.parent))

# Our locale codes -> Google Translate's (None: Google doesn't have it)
GOOGLE_LANG: dict[str, str | None] = {
    "en_GB": None,  # kept by hand
    "zh_CN": "zh-CN",
    "hi_IN": "hi",
    "es_ES": "es",
    "ar_MSA": "ar",
    "fr_FR": "fr",
    "bn_BD": "bn",
    "pt_BR": "pt",
    "id_ID": "id",
    "ur_PK": "ur",
    "ru_RU": "ru",
    "de_DE": "de",
    "ja_JP": "ja",
    "pcm_NG": None,
    "vi_VN": "vi",
    "ko_KR": "ko",
    "it_IT": "it",
    "tr_TR": "tr",
    "pl_PL": "pl",
    "nl_NL": "nl",
    "th_TH": "th",
    "fa_IR": "fa",
    "sw_KE": "sw",
    "tl_PH": "tl",
    "uk_UA": "uk",
    "ro_RO": "ro",
    "ms_MY": "ms",
    "zh_TW": "zh-TW",
    "cs_CZ": "cs",
    "el_GR": "el",
    "hu_HU": "hu",
}

_URL = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&dt=t&tl="
_PLACEHOLDER = re.compile(r"\{[a-zA-Z_0-9]*\}")
_MAX_CHARS = 3000

# Serbian: Google writes Cyrillic; the Latin alphabet maps letter for letter
_SR_LATIN = dict(
    zip(
        "АБВГДЂЕЖЗИЈКЛЉМНЊОПРСТЋУФХЦЧЏШабвгдђежзијклљмнњопрстћуфхцчџш",
        [
            "A",
            "B",
            "V",
            "G",
            "D",
            "Đ",
            "E",
            "Ž",
            "Z",
            "I",
            "J",
            "K",
            "L",
            "Lj",
            "M",
            "N",
            "Nj",
            "O",
            "P",
            "R",
            "S",
            "T",
            "Ć",
            "U",
            "F",
            "H",
            "C",
            "Č",
            "Dž",
            "Š",
            "a",
            "b",
            "v",
            "g",
            "d",
            "đ",
            "e",
            "ž",
            "z",
            "i",
            "j",
            "k",
            "l",
            "lj",
            "m",
            "n",
            "nj",
            "o",
            "p",
            "r",
            "s",
            "t",
            "ć",
            "u",
            "f",
            "h",
            "c",
            "č",
            "dž",
            "š",
        ],
        strict=True,
    )
)

# Spanish for Latin America: Google writes Spain's words for these
_LATAM = (
    ("vídeos", "videos"),
    ("vídeo", "video"),
    ("Vídeo", "Video"),
    ("ordenador", "computadora"),
)


def _post(google_code: str, text: str) -> str:
    data = urllib.parse.urlencode({"q": text}).encode()
    req = urllib.request.Request(
        _URL + urllib.parse.quote(google_code),
        data=data,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return "".join(seg[0] for seg in body[0] if seg and seg[0])


def _translate_lines(google_code: str, lines: list[str]) -> list[str] | None:
    """*lines* translated, the same number back (None if Google merged or split some).
    A refusal (e.g. 429 Too Many Requests) is waited out a few times, then raised:
    trying again in smaller pieces would only ask more often."""
    for attempt in range(4):
        try:
            out = _post(google_code, "\n".join(lines)).split("\n")
            return out if len(out) == len(lines) else None
        except Exception as exc:
            if attempt == 3:
                raise
            print(f"      ({exc}; waiting)", flush=True)
            time.sleep(30 * (attempt + 1))
    return None


def _protect(text: str) -> tuple[str, list[str]]:
    """{placeholders} -> {0}, {1}... (a number in braces is left alone)."""
    found: list[str] = []

    def number(m: re.Match[str]) -> str:
        found.append(m.group(0))
        return "{" + str(len(found) - 1) + "}"

    return _PLACEHOLDER.sub(number, text), found


def _restore(text: str, found: list[str]) -> str:
    for i, tok in enumerate(found):
        text = text.replace("{" + str(i) + "}", tok).replace("{ " + str(i) + " }", tok)
    return text


def _ending(en: str, tr: str, locale: str) -> str:
    """Keep the English's closing "…" or "?" (and add none it hasn't)."""
    tr = tr.rstrip()
    ends = ("…", "...")
    if en.rstrip().endswith(ends) and not tr.endswith(ends):
        tr = tr.rstrip(".。") + "…"
    elif not en.rstrip().endswith(ends) and tr.endswith(ends):
        tr = tr.rstrip(".…")
    q = ("?", "？", "؟")
    if en.rstrip().endswith("?") and not tr.endswith(q):
        tr = tr.rstrip(".。") + ("؟" if locale in ("ar_MSA", "fa_IR", "ur_PK", "pnb_PK") else "?")
    elif not en.rstrip().endswith("?") and tr.endswith(q):
        tr = tr.rstrip("?？؟")
    return tr


def _fits(en: str, tr: str) -> bool:
    return bool(tr.strip()) and sorted(_PLACEHOLDER.findall(tr)) == sorted(_PLACEHOLDER.findall(en))


def _batches(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    out: list[list[tuple[str, str]]] = [[]]
    size = 0
    for item in items:
        if out[-1] and size + len(item[1]) > _MAX_CHARS:
            out.append([])
            size = 0
        out[-1].append(item)
        size += len(item[1]) + 1
    return [b for b in out if b]


def translate_strings(google_code: str, en: dict[str, str]) -> dict[str, str]:
    """{key: English} -> {key: translation} (keys that failed left out)."""
    # Every line of every string, placeholders numbered, outer spaces kept aside
    lines: list[tuple[str, str]] = []  # (key#line, text)
    shapes: dict[str, tuple[list[str], list[tuple[str, str]]]] = {}
    for key, text in en.items():
        protected, found = _protect(text)
        parts = protected.split("\n")
        pads = []
        for i, part in enumerate(parts):
            core = part.strip()
            lead = part[: len(part) - len(part.lstrip())]
            trail = part[len(part.rstrip()) :]
            pads.append((lead, trail))
            if core:
                lines.append((f"{key}#{i}", core))
        shapes[key] = (found, pads)
    done: dict[str, str] = {}
    pending = _batches(lines)
    while pending:
        batch = pending.pop(0)
        out = _translate_lines(google_code, [t for _k, t in batch])
        if out is None:
            if len(batch) > 1:
                half = len(batch) // 2
                pending[:0] = [batch[:half], batch[half:]]
            continue
        done.update({k: t.strip() for (k, _t), t in zip(batch, out, strict=True)})
        time.sleep(0.4)  # be polite to the free endpoint
    result: dict[str, str] = {}
    for key, text in en.items():
        found, pads = shapes[key]
        parts = []
        for i, (lead, trail) in enumerate(pads):
            got = done.get(f"{key}#{i}")
            if got is None and text.split("\n")[i].strip():
                break  # a line failed: leave the whole string out
            parts.append(lead + (got or "") + trail)
        else:
            tr = _restore("\n".join(parts), found)
            if _fits(text, tr):
                result[key] = tr
    return result


# ── Local model (OpenAI-compatible server) ───────────────────────────────────

#: the language each locale is, in words a model understands
ENGLISH_NAMES = {
    "pt_BR": "Brazilian Portuguese",
    "zh_CN": "Simplified Chinese",
    "zh_TW": "Traditional Chinese (Taiwan)",
    "ar_MSA": "Modern Standard Arabic",
    "tl_PH": "Filipino",
    "pcm_NG": "Nigerian Pidgin",
}

_PROMPT = """You translate the interface of FFmpeg Workbench, a free desktop app for \
editing and converting video and audio, from English into {language}.

Rules:
- Natural, concise wording a native speaker would expect in software menus, \
buttons and messages. Use the usual computing terms of {language}.
- Keep exactly as they are: {{placeholders}} in curly braces, \\n line breaks \
(same number), emoji and symbols, a closing "…", "?" or ":", program and \
format names (FFmpeg, yt-dlp, H.264, HEVC, AV1, VP9, MP4, MKV, WebM, GIF, \
LUT, HDR, SDR, CRF, FPS, GPU, NVENC, YouTube, X/Twitter, TikTok...).
- Translate every string; never leave one in English unless it is only names \
or numbers.
- Reply with ONLY a JSON object with the same keys and the translations as \
values. No comments, no code fences.

{batch}"""


def _local_language(locale: str) -> str:
    from core.i18n import LANGUAGE_NAMES

    return ENGLISH_NAMES.get(locale) or f"{LANGUAGE_NAMES[locale]} ({locale})"


def _ask_local(url: str, prompt: str) -> str:
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 6000,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=1800) as resp:
        reply = json.loads(resp.read().decode("utf-8"))
    return reply["choices"][0]["message"]["content"]


def _json_object(text: str) -> dict[str, object]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _fits_local(en: str, tr: object) -> bool:
    return (
        isinstance(tr, str)
        and _fits(en, tr)
        and tr.count("\n") == en.count("\n")
        and not (tr.strip() == en.strip() and len(en.split()) > 2)
    )


def translate_local(
    url: str,
    locale: str,
    en: dict[str, str],
    size: int = 40,
    save: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, str]:
    """{key: English} -> {key: translation} from the model at *url* (failures left out:
    a batch's failed strings are asked again in smaller batches, then alone)."""
    language = _local_language(locale)
    result: dict[str, str] = {}
    keys = list(en)
    pending = [keys[i : i + size] for i in range(0, len(keys), size)]
    tries: dict[str, int] = {}
    while pending:
        batch = pending.pop(0)
        started = time.monotonic()
        prompt = _PROMPT.format(
            language=language,
            batch=json.dumps({k: en[k] for k in batch}, ensure_ascii=False, indent=1),
        )
        try:
            got = _json_object(_ask_local(url, prompt))
        except Exception as exc:  # noqa: BLE001 - a dev script: report, then retry once
            print(f"      ({exc})", flush=True)
            time.sleep(10)
            got = {}
        good = {k: str(got[k]).strip() for k in batch if k in got and _fits_local(en[k], got[k])}
        result.update(good)
        if save is not None and good:
            save(good)  # so a stopped run carries on where it was
        bad = [k for k in batch if k not in good]
        print(
            f"    {len(result)}/{len(en)}  (+{len(good)}, {len(bad)} to redo, "
            f"{time.monotonic() - started:.0f}s)",
            flush=True,
        )
        for k in bad:
            tries[k] = tries.get(k, 0) + 1
        bad = [k for k in bad if tries[k] < 3]
        if bad:
            step = max(1, len(bad) // 2) if len(bad) > 4 else len(bad)
            pending[:0] = [bad[i : i + step] for i in range(0, len(bad), step)]
    return result


def _post_process(locale: str, strings: dict[str, str], en: dict[str, str]) -> dict[str, str]:
    out = {}
    for key, tr in strings.items():
        if locale == "sr_Latn":
            tr = "".join(_SR_LATIN.get(c, c) for c in tr)
        if locale == "es_419":
            for spain, latam in _LATAM:
                tr = tr.replace(spain, latam)
        out[key] = _ending(en[key], tr, locale)
    return out


def load_json(path: Path) -> dict[str, object]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, object], quiet: bool = False) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)
    if not quiet:
        print(f"  saved {path.name}")


def process_language(
    locale: str, google_code: str, en: dict[str, str], dry_run: bool, local: str = ""
) -> None:
    from core.i18n import LANGUAGE_NAMES, RTL_LANGUAGES

    path = LOCALE_DIR / f"{locale}.json"
    existing = load_json(path) if path.exists() else {}
    meta = existing.pop("_meta", None) or {
        "language": LANGUAGE_NAMES[locale],
        "code": locale,
        "rtl": locale in RTL_LANGUAGES,
    }
    missing = {k: v for k, v in en.items() if k not in existing}
    if not missing:
        print(f"  {locale}: already complete ({len(existing)} keys)")
        return
    print(f"  {locale}: {len(missing)} strings to translate…", flush=True)
    if dry_run:
        return

    def save(new: dict[str, str], quiet: bool = True) -> None:
        existing.update(_post_process(locale, new, en))
        save_json(path, {"_meta": meta, **{k: existing[k] for k in en if k in existing}}, quiet)

    raw = (
        translate_local(local, locale, missing, save=save)
        if local
        else translate_strings(google_code, missing)
    )
    save(raw, quiet=False)
    print(f"  {locale}: {len(raw)} translated, {len(missing) - len(raw)} left in English")


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate missing locale keys")
    parser.add_argument("--lang", action="append", help="only this language (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="show what would be done")
    parser.add_argument(
        "--local",
        nargs="?",
        const="http://127.0.0.1:5001",
        default="",
        help="use a local OpenAI-compatible model server (default KoboldCpp, port 5001)",
    )
    args = parser.parse_args()

    en = {k: v for k, v in load_json(LOCALE_DIR / "en_US.json").items() if isinstance(v, str)}
    usable = [c for c, g in GOOGLE_LANG.items() if g or (args.local and c != "en_GB")]
    for lang in args.lang or usable:
        gcode = GOOGLE_LANG.get(lang) or ""
        if not (gcode or args.local):
            print(f"  {lang}: no Google Translate code - skipped")
            continue
        process_language(lang, gcode, en, args.dry_run, args.local)
    print("\nDone.  Now run scripts/locale_lint.py")


if __name__ == "__main__":
    main()
