"""
main.py - WyldVeil's Media Editor: where it starts.

``main.py --cli ...`` (or an executable whose name ends in ``-cli``) runs the
command line (core/cli.py) and never loads the window code; anything else
opens the window (shell/app.py).
"""
from __future__ import annotations

import os
import sys

# The embeddable Python used by the Windows installer runs with a ._pth file,
# which leaves this folder off sys.path: put it back first.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _wants_cli(argv: list[str]) -> bool:
    exe = os.path.splitext(os.path.basename(sys.executable))[0].lower()
    return argv[:1] == ["--cli"] or exe.endswith("-cli")


def main(argv: list[str] | None = None) -> int:
    args_in = sys.argv[1:] if argv is None else argv
    if _wants_cli(args_in):
        from core.cli import entry
        return entry(args_in)

    import argparse
    import multiprocessing
    multiprocessing.freeze_support()                 # PyInstaller on Windows
    from core import APP_NAME
    parser = argparse.ArgumentParser(prog="wyldveil-media-editor", description=APP_NAME)
    parser.add_argument("files", nargs="*", help="media files to open")
    parser.add_argument("--debug", action="store_true", help="verbose logging to the console")
    parser.add_argument("--self-test", action="store_true", help="check the installation and exit")
    parser.add_argument("--no-ai", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cli", action="store_true", help="command-line mode, no window (see AGENTS.md); "
                                                            "must come first")
    args, _unknown = parser.parse_known_args(args_in)
    if args.no_ai:
        os.environ["CROSSFADER_NO_AI_TEST"] = "1"
    if args.self_test:
        os.environ.setdefault("CROSSFADER_NO_SOUND", "1")
        os.environ.setdefault("CROSSFADER_NO_NETWORK", "1")

    _windows_dpi_awareness()
    from core.i18n import init as init_i18n
    init_i18n()                                      # before any widget exists
    from shell.app import App
    app = App()
    if args.self_test:
        from shell import selftest
        app.withdraw()
        code = selftest.run(app)
        app.destroy()
        return code
    if args.files:
        app.after(400, lambda: app.open_paths(args.files))
    app.mainloop()
    return 0


def _windows_dpi_awareness() -> None:
    """Sharp text on Windows: tell it we handle DPI ourselves (core/scaling.py)."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(main())
