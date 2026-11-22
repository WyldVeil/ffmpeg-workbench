"""
shell - the window around the tools (the GUI's composition root).

    app.py         App: builds the window from the parts below and routes between them
    pages.py       PageHost: builds each tool's page on first use, shows one at a time
    sidebar.py     the tool list, sections and search
    statusbar.py   status, render progress and queue, FFmpeg / yt-dlp in use
    menus.py       the menu bar
    shortcuts.py   keyboard shortcuts (through the page contracts in tabs/kit)
    files.py       File › Open, Recent Files, drag-and-drop
    versions.py    FFmpeg and yt-dlp versions and updates
    wheel.py       the mouse wheel scrolls what is under the pointer
    chime.py       the start-up sound
    selftest.py    --self-test

Pages never import from here; they talk to the window through the Shell
protocol in tabs/kit/contracts.py.
"""
