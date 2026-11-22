#!/bin/sh
# Install FFmpeg Workbench for the current user (no root needed).
#   ./install.sh            install / update
#   ./install.sh --uninstall
set -eu
APP=ffmpeg-workbench
APP_ID=io.github.ffmpeg_workbench.FFmpegWorkbench
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
OPT="$HOME/.local/opt/$APP"
BIN="$HOME/.local/bin"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$OPT" "$BIN/$APP" "$PREFIX/applications/$APP_ID.desktop" \
           "$PREFIX/icons/hicolor/scalable/apps/$APP_ID.svg" "$PREFIX/metainfo/$APP_ID.metainfo.xml"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$PREFIX/applications" || true
    echo "Uninstalled. Your settings remain in ~/.config/$APP (delete it to reset)."
    exit 0
fi

mkdir -p "$OPT" "$BIN" "$PREFIX/applications" "$PREFIX/icons/hicolor/scalable/apps" "$PREFIX/metainfo"
rm -rf "$OPT"
cp -a "$HERE/$APP" "$OPT"
ln -sf "$OPT/$APP" "$BIN/$APP"
sed "s|^Exec=.*|Exec=$OPT/$APP %F|; s|^TryExec=.*|TryExec=$OPT/$APP|" \
    "$HERE/$APP_ID.desktop" > "$PREFIX/applications/$APP_ID.desktop"
cp "$HERE/$APP_ID.svg" "$PREFIX/icons/hicolor/scalable/apps/$APP_ID.svg"
cp "$HERE/$APP_ID.metainfo.xml" "$PREFIX/metainfo/"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$PREFIX/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$PREFIX/icons/hicolor" || true
echo "Installed to $OPT"
echo "Start it from your application menu, or run: $APP"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not on your PATH." ;; esac
