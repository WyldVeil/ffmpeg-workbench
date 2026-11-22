"""
core/ops - the FFmpeg command builders behind the tools.

Pure functions (and small value classes): settings in, FFmpeg arguments or a
filter graph out.  No widgets, no Tk, so the window (tabs/) and the command
line (core/cli.py) share exactly the same recipes - a fix in one place fixes
both, and every builder can be tested without a display.

One module per tool family, named after the tool page it came from.
"""
