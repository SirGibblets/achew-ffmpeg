#!/usr/bin/env bash
# Fetch the pinned FFmpeg, fdk-aac and zlib sources into sources/ and verify
# them against versions.env. Safe to re-run: verified sources are reused.
#
#   build/fetch-sources.sh            fetch and verify
#   build/fetch-sources.sh --archive  also write release tarballs to dist/sources/
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../versions.env
. "$ROOT/versions.env"
SRC="$ROOT/sources"
mkdir -p "$SRC"

fetch_git() {
    local name="$1" repo="$2" commit="$3" dir="$SRC/$1"
    if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ]; then
        return
    fi
    rm -rf "$dir"
    git init -q "$dir"
    git -C "$dir" fetch -q --depth 1 "$repo" "$commit"
    git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
    if [ "$(git -C "$dir" rev-parse HEAD)" != "$commit" ]; then
        echo "$name: fetched commit does not match $commit" >&2
        exit 1
    fi
}

sha256() {
    if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

fetch_git ffmpeg "$FFMPEG_REPO" "$FFMPEG_COMMIT"
fetch_git fdk-aac "$FDK_AAC_REPO" "$FDK_AAC_COMMIT"

# A shallow clone has no tags for git describe, so pin the version string
# ffmpeg reports through the VERSION file its build reads instead.
echo "$FFMPEG_VERSION_STRING" > "$SRC/ffmpeg/VERSION"

ZLIB_TARBALL="$SRC/zlib-$ZLIB_VERSION.tar.gz"
if [ ! -f "$ZLIB_TARBALL" ] || [ "$(sha256 "$ZLIB_TARBALL")" != "$ZLIB_SHA256" ]; then
    curl -fsSL -o "$ZLIB_TARBALL.part" "$ZLIB_URL"
    if [ "$(sha256 "$ZLIB_TARBALL.part")" != "$ZLIB_SHA256" ]; then
        echo "zlib: checksum mismatch for $ZLIB_URL" >&2
        exit 1
    fi
    mv "$ZLIB_TARBALL.part" "$ZLIB_TARBALL"
fi

if [ "${1:-}" = "--archive" ]; then
    out="$ROOT/dist/sources"
    mkdir -p "$out"
    git -C "$SRC/ffmpeg" archive --format=tar --prefix="ffmpeg-$FFMPEG_VERSION_STRING/" \
        --add-file="$SRC/ffmpeg/VERSION" HEAD | gzip -n > "$out/ffmpeg-$FFMPEG_VERSION_STRING.tar.gz"
    git -C "$SRC/fdk-aac" archive --format=tar --prefix="fdk-aac-$FDK_AAC_VERSION/" HEAD \
        | gzip -n > "$out/fdk-aac-$FDK_AAC_VERSION.tar.gz"
    cp "$ZLIB_TARBALL" "$out/"
fi
