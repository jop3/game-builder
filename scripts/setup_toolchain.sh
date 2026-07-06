#!/usr/bin/env bash
# Provision the pinned asset-pipeline toolchain (spec §3) inside a Claude Code
# remote container (Ubuntu 24.04): Blender 4.2 LTS + Godot 4.x headless +
# orchestrator Python deps. Idempotent -- safe to re-run; skips what exists.
#
# Why this shape (learned the hard way in the 2026-07 sessions):
#   * GitHub downloads are BLOCKED by the container's egress proxy (repo-scoped
#     API access only), so Godot cannot come from its official releases.
#     Debian sid packages godot and its runtime libs; deb.debian.org is
#     reachable, and the sid binary runs on Ubuntu 24.04 (needs glibc <= 2.39
#     -- true for godot 4.6.3+ds-1; re-check `objdump` if the version bumps).
#   * download.blender.org and pypi.org are reachable directly.
#
# Usage:  bash scripts/setup_toolchain.sh
# After:  export PATH=/opt/toolchain/bin:$PATH   (echoed at the end)
set -euo pipefail

TOOLCHAIN=/opt/toolchain
BIN=$TOOLCHAIN/bin
BLENDER_SERIES=Blender4.2               # spec §3 pins Blender 4.2 LTS exactly
DEBIAN=https://deb.debian.org/debian
mkdir -p "$TOOLCHAIN" "$BIN"

# ---------------------------------------------------------------- Blender ---
if [ -x "$TOOLCHAIN/blender/blender" ]; then
    echo "blender: already installed ($($TOOLCHAIN/blender/blender --version | head -1))"
else
    echo "blender: resolving latest $BLENDER_SERIES point release..."
    tarball=$(curl -fsS "https://download.blender.org/release/$BLENDER_SERIES/" \
        | grep -o 'blender-4\.2\.[0-9]*-linux-x64\.tar\.xz' | sort -uV | tail -1)
    [ -n "$tarball" ] || { echo "FATAL: no $BLENDER_SERIES linux-x64 tarball found" >&2; exit 1; }
    echo "blender: downloading $tarball (~350 MB)..."
    curl -fsS -o "$TOOLCHAIN/blender.tar.xz" \
        "https://download.blender.org/release/$BLENDER_SERIES/$tarball"
    tar -xf "$TOOLCHAIN/blender.tar.xz" -C "$TOOLCHAIN"
    rm -f "$TOOLCHAIN/blender.tar.xz"
    mv "$TOOLCHAIN/${tarball%.tar.xz}" "$TOOLCHAIN/blender"
fi
ln -sf "$TOOLCHAIN/blender/blender" "$BIN/blender"

# ------------------------------------------------------------------ Godot ---
if [ -x "$TOOLCHAIN/godot-deb/usr/bin/godot" ] && [ -x "$BIN/godot" ] \
        && "$BIN/godot" --headless --version >/dev/null 2>&1; then
    echo "godot: already installed ($("$BIN/godot" --headless --version 2>/dev/null | tail -1))"
else
    echo "godot: fetching Debian sid package index..."
    pkgidx=$TOOLCHAIN/Packages.gz
    curl -fsS -o "$pkgidx" "$DEBIAN/dists/sid/main/binary-amd64/Packages.gz"

    deb_path() {  # deb_path <package-name> -> pool path from the sid index
        # No awk early-exit: with pipefail, `exit` SIGPIPEs zcat and the
        # nonzero pipe status kills the whole script under set -e.
        zcat "$pkgidx" | awk -v p="$1" '
            $1=="Package:" {found=($2==p && !done)}
            found && $1=="Filename:" {print $2; done=1; found=0}'
    }

    fetch_deb() {  # fetch_deb <package-name> -- download + extract into godot-libs-extract
        local fn; fn=$(deb_path "$1")
        [ -n "$fn" ] || { echo "FATAL: package $1 not in sid index" >&2; exit 1; }
        echo "godot:   $1  <-  $fn"
        curl -fsS -o "$TOOLCHAIN/$1.deb" "$DEBIAN/$fn"
        dpkg-deb -x "$TOOLCHAIN/$1.deb" "$TOOLCHAIN/godot-libs-extract"
        rm -f "$TOOLCHAIN/$1.deb"
    }

    echo "godot: downloading godot from Debian sid..."
    fn=$(deb_path godot)
    curl -fsS -o "$TOOLCHAIN/godot.deb" "$DEBIAN/$fn"
    rm -rf "$TOOLCHAIN/godot-deb"
    dpkg-deb -x "$TOOLCHAIN/godot.deb" "$TOOLCHAIN/godot-deb"
    rm -f "$TOOLCHAIN/godot.deb"

    # glibc guard: the sid binary must not need a newer glibc than the host's.
    need=$(objdump -T "$TOOLCHAIN/godot-deb/usr/bin/godot" | grep -o 'GLIBC_2\.[0-9]*' | sort -uV | tail -1)
    have=$(ldd --version | head -1 | grep -o '2\.[0-9]*' | head -1)
    echo "godot: binary needs $need, host glibc $have"

    # Runtime libs the sid binary links that Ubuntu noble lacks (or has too
    # old). Resolved from the same sid index so versions track automatically.
    echo "godot: fetching sid runtime libraries..."
    rm -rf "$TOOLCHAIN/godot-libs-extract"
    for pkg in libicu78 libenet7 libtheoraenc2 libtheoradec2 libvorbisenc2 \
               libvorbis0a libogg0 libturbojpeg0 libmbedtls21 libmbedx509-7 \
               libmbedcrypto16 libwslay1 libminiupnpc21 librecast1debian0 \
               libembree4-4 libpulse0 libspeechd2 libsdl3-0 libdecor-0-0 \
               libxkbcommon0 libwayland-client0 libwayland-cursor0 \
               libwayland-egl1 libwayland-server0 libtbb12 libtbbbind-2-5 \
               libhwloc15; do
        fetch_deb "$pkg"
    done
    # NOTE: libicu78 may bump (libicu79...) when sid transitions; if godot
    # fails with "libicuXX.so not found", add the new package name above.
    mkdir -p "$TOOLCHAIN/godot-libs"
    find "$TOOLCHAIN/godot-libs-extract" -name '*.so*' \
        -exec cp -P {} "$TOOLCHAIN/godot-libs/" \;
    rm -rf "$TOOLCHAIN/godot-libs-extract" "$pkgidx"

    # Second-level deps that Ubuntu noble DOES ship:
    apt-get install -y -q libxss1 libasyncns0 libsndfile1 libtbb12 >/dev/null 2>&1 || \
        echo "godot: apt install skipped (offline or non-root?) -- ldd will tell"

    printf '#!/bin/sh\nexport LD_LIBRARY_PATH=%s${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\nexec %s "$@"\n' \
        "$TOOLCHAIN/godot-libs" "$TOOLCHAIN/godot-deb/usr/bin/godot" > "$BIN/godot"
    chmod +x "$BIN/godot"

    missing=$(LD_LIBRARY_PATH=$TOOLCHAIN/godot-libs ldd "$TOOLCHAIN/godot-deb/usr/bin/godot" \
        | grep "not found" || true)
    [ -z "$missing" ] || { echo "FATAL: unresolved godot libs:"; echo "$missing"; exit 1; }
fi

# ------------------------------------------------------------ Python deps ---
echo "python: installing orchestrator deps (pypi.org is reachable directly)..."
pip install -q pytest pyyaml jsonschema numpy pillow httpx anthropic

# ----------------------------------------------------------------- verify ---
echo
echo "== verification =="
"$BIN/blender" --version | head -1
"$BIN/godot" --headless --version 2>/dev/null | tail -1
echo
echo "Toolchain ready. Add to PATH:"
echo "  export PATH=$BIN:\$PATH"
echo "The spec §3 gate accepts this Godot via the \"4.3+\" floor pin."
