#!/bin/sh
# Builds Tk with anti-aliased fonts (Xft + fontconfig) for the Python bundled
# with the Linux downloads.  The standalone Python ships a Tk without Xft, which
# can only use the X server's old bitmap fonts.
#
# Runs inside an AlmaLinux 8 container (glibc 2.28), so the libraries work on
# any distribution from 2018 on.  Tk links to the system's libX11, libXft,
# fontconfig and freetype, so text looks the same as in other applications.
# packaging/release.py runs it like this:
#
#   podman run --rm -v SRC:/src:ro -v OUT:/out almalinux:8 sh /src/build-tk.sh 9.0.4 2.3.8
#
# SRC holds tcl<V>-src.tar.gz, tk<V>-src.tar.gz, libXft-<XFT>.tar.xz and this
# script.  OUT receives libtcl9tk<major.minor>.so, the tk<major.minor> script
# library, and fallback/: libXft (built from source - versions before 2.3.5
# crash on colour emoji), libXrender, libXcursor and libXfixes, which the
# portable download uses only on a system that doesn't have them.
set -eu
V="$1"
XFT="$2"
MM="${V%.*}"
dnf -y -q install gcc make xz libX11-devel libXft-devel libXrender-devel libXcursor libXfixes \
    fontconfig-devel freetype-devel zlib-devel >/dev/null
mkdir -p /build && cd /build
tar -xzf "/src/tcl$V-src.tar.gz"
tar -xzf "/src/tk$V-src.tar.gz"
tar -xJf "/src/libXft-$XFT.tar.xz"
# The same options as the standalone Python's own Tcl/Tk (python-build-standalone),
# plus Xft.  Tk talks to Tcl through its stubs table, so it works with that Python's libtcl.
rm -rf "/build/tcl$V/pkgs/"*                # Tcl's extra packages are not needed
cd "/build/tcl$V/unix"
./configure --prefix=/opt/tcltk --enable-shared --disable-zipfs >/dev/null
make -j"$(nproc)" binaries >/dev/null
cd "/build/tk$V/unix"
./configure --prefix=/opt/tcltk --with-tcl="/build/tcl$V/unix" --enable-shared \
    --disable-zipfs --enable-xft --disable-xss >/dev/null
make -j"$(nproc)" binaries >/dev/null
make install-binaries install-libraries >/dev/null
cp "/opt/tcltk/lib/libtcl9tk$MM.so" /out/
cp -r "/opt/tcltk/lib/tk$MM" /out/
# Fallback X libraries
cd "/build/libXft-$XFT"
./configure --prefix=/opt/xft --disable-static >/dev/null
make -j"$(nproc)" >/dev/null
make install >/dev/null
mkdir -p /out/fallback
cp -L /opt/xft/lib/libXft.so.2 /out/fallback/
for lib in libXrender.so.1 libXcursor.so.1 libXfixes.so.3; do
    cp -L "/usr/lib64/$lib" /out/fallback/
done
echo "built Tk $V with Xft, and fallback libXft $XFT"
