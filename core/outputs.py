"""
core/outputs.py - Output file naming and overwrite safety.

Rules every tab follows:

* The suggested output goes next to the source (or into the user's default
  output folder) as ``<name>_<suffix>.<ext>`` and never collides with an
  existing file unless the user agrees.
* An output that resolves to one of the inputs is refused - FFmpeg would
  truncate the source while still reading it.
* Missing extensions are added; missing folders are created.

The overwrite policy comes from the ``overwrite_policy`` setting:
``"ask"`` (default), ``"rename"`` (auto-number), or ``"overwrite"``.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable

from core import settings
from core.errors import OutputError
from core.paths import is_writable_dir, user_videos_dir


def default_output_folder(src: str | None = None) -> str:
    folder = settings.DEFAULT_OUTPUT_FOLDER.get().strip()
    if folder and os.path.isdir(folder):
        return folder
    if src:
        d = os.path.dirname(os.path.abspath(src))
        if is_writable_dir(d):
            return d
    return user_videos_dir()


def unique_path(path: str) -> str:
    """``clip.mp4`` -> ``clip (2).mp4`` (then 3, 4 …) until it does not exist."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    m = re.match(r"^(.*) \((\d+)\)$", base)
    if m:
        base, n = m.group(1), int(m.group(2)) + 1
    else:
        n = 2
    while True:
        cand = f"{base} ({n}){ext}"
        if not os.path.exists(cand):
            return cand
        n += 1


def suggest_output(src: str | None, suffix: str = "edited", ext: str | None = None,
                   *, folder: str | None = None, unique: bool = True) -> str:
    """Suggest an output path derived from *src*.

    *ext* may be given with or without a dot; by default the source's
    extension is kept (or ``.mp4`` when there is no source).
    """
    stem = os.path.splitext(os.path.basename(src))[0] if src else "output"
    if ext is None:
        ext = os.path.splitext(src)[1] if src else ".mp4"
    if ext and not ext.startswith("."):
        ext = "." + ext
    folder = folder or default_output_folder(src)
    name = f"{stem}_{suffix}{ext}" if suffix else f"{stem}{ext}"
    path = os.path.join(folder, name)
    return unique_path(path) if unique else path


def same_file(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def ensure_extension(path: str, default_ext: str = ".mp4") -> str:
    if not os.path.splitext(path)[1]:
        return path + (default_ext if default_ext.startswith(".") else "." + default_ext)
    return path


class OutputRejected(OutputError):
    """Raised by :func:`resolve_output` when the user cancels or the path is unusable."""


def resolve_output(out: str, inputs: Iterable[str] = (), *, default_ext: str = ".mp4",
                   policy: str | None = None, parent=None, ask=True) -> str:
    """Validate and finalise an output path before rendering.

    Returns the path to use (possibly renamed), or raises
    :class:`OutputRejected` with a user-facing message.
    """
    out = (out or "").strip().strip('"')
    if not out:
        raise OutputRejected("Choose where to save the output first.")
    out = ensure_extension(os.path.abspath(os.path.expanduser(out)), default_ext)
    for src in inputs:
        if src and same_file(out, src):
            alt = unique_path(out)
            raise OutputRejected(
                "The output file is the same as an input file, which would destroy "
                f"the source while reading it.\n\nPick another name, e.g.\n{alt}")
    folder = os.path.dirname(out)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise OutputRejected(f"Cannot create the output folder:\n{folder}\n\n{exc}") from exc
    if not is_writable_dir(folder):
        raise OutputRejected(f"The output folder is not writable:\n{folder}")
    if os.path.isdir(out):
        raise OutputRejected(f"The output path is a folder:\n{out}")
    if not os.path.exists(out):
        return out
    policy = policy or settings.OVERWRITE_POLICY.get()
    if policy == "overwrite":
        return out
    if policy == "rename" or not ask:
        return unique_path(out)
    from tkinter import messagebox
    alt = unique_path(out)
    answer = messagebox.askyesnocancel(
        "File already exists",
        f"{os.path.basename(out)} already exists.\n\n"
        f"Yes — overwrite it\nNo — save as {os.path.basename(alt)}\n"
        "Cancel — don't render",
        parent=parent)  # pyright: ignore[reportArgumentType] - None means "the main window", as Tk allows
    if answer is None:
        raise OutputRejected("")
    return out if answer else alt
