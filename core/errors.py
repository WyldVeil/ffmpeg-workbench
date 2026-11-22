"""
core/errors.py - the app's exceptions, all under one root.

Effective Python item 121: one root exception lets every caller tell an
*expected* failure (a missing file, a broken download, a cancelled render)
from a *bug*.  The window shows an AppError's message as it is, in words a
person can act on; anything else is a bug and gets the bug-report dialog.

    AppError
    ├── InputError          what the person chose can't be used
    ├── MediaError
    │   └── ProbeError      ffprobe couldn't read a file
    ├── ProcessError        a program (FFmpeg, yt-dlp...) failed
    │   ├── StartError      (also an OSError)
    │   │   └── ProgramNotFound
    │   ├── ProcessTimeout
    │   └── CommandError    a command ran and failed
    ├── Cancelled           the person stopped it (not a failure)
    ├── NetworkError
    │   └── OfflineError
    ├── OutputError         where the result would go is not allowed
    └── ToolError           a tool's own expected failure (TTS, CLI...)

Classes that existed before keep their names, and their old base classes too
(RuntimeError, OSError, FileNotFoundError, TimeoutError) so handlers written
for those still catch them.  Always ``raise NewError(...) from exc``.
"""
from __future__ import annotations


class AppError(Exception):
    """An expected failure whose message is fit to show as it is."""


class InputError(AppError, ValueError):
    """The input or a setting the person chose can't be used."""


class MediaError(AppError):
    """A media file is missing, unreadable or not the kind that was needed."""


class ProbeError(MediaError, RuntimeError):
    """ffprobe could not read a file."""


class ProcessError(AppError, RuntimeError):
    """An external program could not be run, or failed."""


class StartError(ProcessError, OSError):
    """A program could not be started at all."""


class ProgramNotFound(StartError, FileNotFoundError):
    """The program isn't installed (or its configured path is wrong)."""


class ProcessTimeout(ProcessError, TimeoutError):
    """A program ran longer than it was allowed to."""


class CommandError(ProcessError):
    """A program ran and exited with an error; the message says why."""


class Cancelled(AppError):
    """The person stopped the work.  Not a failure: show nothing alarming."""


class NetworkError(AppError, OSError):
    """A network request failed."""


class OfflineError(NetworkError):
    """The network is not reachable at all."""


class OutputError(AppError):
    """The output can't be written where it was asked to go."""


class ToolError(AppError, RuntimeError):
    """A tool's own expected failure (e.g. a voice that can't speak)."""
