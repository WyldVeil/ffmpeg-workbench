"""
core/whisper.py - local speech-to-text with whisper.cpp.

The small ``whisper-cli`` program is bundled (bin/<platform>/whisper, MIT
licence); the speech models are downloaded on first use from the
whisper.cpp model repository into the app's models folder and verified
against pinned SHA-256 sums.  Everything runs offline after that.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"


@dataclass(frozen=True)
class Model:
    key: str
    label: str
    file: str
    size_mb: int
    sha256: str


MODELS = {
    m.key: m
    for m in (
        Model(
            "tiny",
            "Tiny - fastest, rough (32 MB)",
            "ggml-tiny-q5_1.bin",
            32,
            "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7",
        ),
        Model(
            "base",
            "Base - fast (60 MB)",
            "ggml-base-q5_1.bin",
            60,
            "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
        ),
        Model(
            "small",
            "Small - good balance (190 MB)",
            "ggml-small-q5_1.bin",
            190,
            "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
        ),
        Model(
            "turbo",
            "Large v3 Turbo - most accurate (574 MB)",
            "ggml-large-v3-turbo-q5_0.bin",
            574,
            "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
        ),
    )
}
DEFAULT_MODEL = "small"

LANGUAGES = [
    ("auto", "Detect automatically"),
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("nl", "Dutch"),
    ("pl", "Polish"),
    ("ru", "Russian"),
    ("uk", "Ukrainian"),
    ("tr", "Turkish"),
    ("ar", "Arabic"),
    ("fa", "Persian"),
    ("hi", "Hindi"),
    ("bn", "Bengali"),
    ("ur", "Urdu"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("vi", "Vietnamese"),
    ("th", "Thai"),
    ("id", "Indonesian"),
    ("ms", "Malay"),
    ("tl", "Tagalog"),
    ("sw", "Swahili"),
    ("ro", "Romanian"),
    ("el", "Greek"),
    ("cs", "Czech"),
    ("sv", "Swedish"),
    ("da", "Danish"),
    ("no", "Norwegian"),
    ("fi", "Finnish"),
    ("hu", "Hungarian"),
    ("he", "Hebrew"),
]

_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)%")


def cli_path() -> str | None:
    from core.hardware import find_binary

    return find_binary("whisper-cli")


def models_folder() -> str:
    from core.paths import models_dir

    d = os.path.join(models_dir(), "whisper")
    os.makedirs(d, exist_ok=True)
    return d


def model_path(key: str) -> str:
    return os.path.join(models_folder(), MODELS[key].file)


def is_installed(key: str) -> bool:
    """Present and verified (a '.ok' marker is written after the SHA-256 check)."""
    path = model_path(key)
    return os.path.isfile(path) and os.path.isfile(path + ".ok")


def adopt_file(key: str, path: str | None = None) -> bool:
    """Install model *key* from a file the person copied in by hand (offline
    machines): accepted only if its SHA-256 matches the pinned one."""
    m = MODELS[key]
    src = path or model_path(key)
    if not os.path.isfile(src):
        return False
    h = hashlib.sha256()
    with open(src, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    if h.hexdigest() != m.sha256:
        return False
    dest = model_path(key)
    if os.path.abspath(src) != os.path.abspath(dest):
        import shutil

        shutil.copyfile(src, dest + ".part")
        os.replace(dest + ".part", dest)
    with open(dest + ".ok", "w", encoding="utf-8") as fh:
        fh.write(m.sha256 + "\n")
    return True


def download_model(
    key: str,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Download and verify model *key*; returns its path.  Raises RuntimeError."""
    from core.updater import offline, open_verified

    m = MODELS[key]
    dest = model_path(key)
    if is_installed(key) or adopt_file(key):
        return dest
    if offline():
        raise RuntimeError(
            f"Offline mode is on. To install this model by hand, put {m.file} "
            f"(from {BASE_URL}{m.file}) in {models_folder()} and choose it again."
        )
    part = dest + ".part"
    h = hashlib.sha256()
    done = 0
    try:
        from core import network

        with (
            open_verified(network.mirror_url(BASE_URL + m.file), timeout=60) as r,
            open(part, "wb") as fh,
        ):
            total = int(r.headers.get("Content-Length") or 0) or m.size_mb * 1_000_000
            while True:
                if cancelled is not None and cancelled():
                    raise RuntimeError("Download cancelled.")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(min(1.0, done / total))
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(part)
        raise
    if h.hexdigest() != m.sha256:
        os.remove(part)
        raise RuntimeError("The downloaded model failed its checksum - please try again.")
    os.replace(part, dest)
    with open(dest + ".ok", "w", encoding="ascii") as fh:
        fh.write(m.sha256 + "\n")
    return dest


def threads() -> int:
    return max(1, min(16, (os.cpu_count() or 4) - 1))


def transcribe_cmd(
    audio_wav: str,
    model: str,
    out_base: str,
    *,
    language: str = "auto",
    translate: bool = False,
    max_len: int = 42,
    formats: Sequence[str] = ("srt",),
    prompt: str = "",
) -> list[str]:
    """whisper-cli argv: writes ``out_base.<fmt>`` for each format."""
    cli = cli_path()
    if not cli:
        raise RuntimeError("The speech-to-text engine (whisper-cli) is missing from this build.")
    cmd = [
        cli,
        "-m",
        model,
        "-f",
        audio_wav,
        "-of",
        out_base,
        "-l",
        language or "auto",
        "-t",
        str(threads()),
        "-pp",
    ]
    for fmt in formats:
        cmd.append(
            {"srt": "-osrt", "vtt": "-ovtt", "txt": "-otxt", "json": "-ojf", "lrc": "-olrc"}[fmt]
        )
    if max_len:
        cmd += ["-ml", str(int(max_len)), "-sow"]
    if translate:
        cmd.append("-tr")
    if prompt.strip():
        cmd += ["--prompt", prompt.strip()]
    return cmd


def parse_progress(line: str) -> float | None:
    m = _PROGRESS_RE.search(line or "")
    return int(m.group(1)) / 100 if m else None


def audio_extract_cmd(
    src: str, wav: str, *, start: float = 0.0, duration: float | None = None, stream: int = 0
) -> list[str]:
    """16 kHz mono PCM, the format whisper.cpp reads."""
    from core import ffmpeg as ff

    cmd = [ff.ffmpeg_path(), "-y"]
    if start > 0:
        cmd += ["-ss", ff.ts(start)]
    if duration:
        cmd += ["-t", ff.ts(duration)]
    return [
        *cmd,
        "-i",
        src,
        "-map",
        f"0:a:{stream}",
        "-vn",
        "-sn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        wav,
    ]
