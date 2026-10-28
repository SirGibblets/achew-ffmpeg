#!/usr/bin/env bash
# Build the lean LGPL ffmpeg (with libfdk_aac) for one target.
#
#   build/build.sh <target> [out_dir]
#
# Targets and where they run:
#   linux-x86_64, linux-aarch64   inside build/linux.Dockerfile (musl, fully static)
#   macos-arm64, macos-x86_64     on a macOS host; x86_64 cross-compiles and needs nasm
#   windows-x86_64                inside build/windows.Dockerfile (mingw-w64 cross, static)
#
# Writes <out_dir>/ffmpeg[.exe], <out_dir>/licenses/ and <out_dir>/build.json.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../versions.env
. "$ROOT/versions.env"

TARGET="${1:?usage: build/build.sh <target> [out_dir]}"
OUT="${2:-$ROOT/out/$TARGET}"
WORK="$ROOT/work/$TARGET"
PREFIX="$WORK/prefix"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN)}"

EXE=ffmpeg
CROSS=""
BUILD_ZLIB=1
CFLAGS_EXTRA=""
LDFLAGS_EXTRA=""
CMAKE_ARGS=()
FF_ARGS=()

case "$TARGET" in
    linux-x86_64 | linux-aarch64)
        # musl's default thread stack is only 128 KiB, and ffmpeg runs codecs in
        # their own threads; fdk-aac keeps large scratch buffers on the stack.
        # Match glibc-like headroom (musl reads the size from PT_GNU_STACK).
        LDFLAGS_EXTRA="-static -Wl,-z,stack-size=2097152"
        FF_ARGS+=(--enable-pthreads)
        ;;
    macos-arm64 | macos-x86_64)
        arch="${TARGET#macos-}"
        export MACOSX_DEPLOYMENT_TARGET=11.0
        CFLAGS_EXTRA="-arch $arch -mmacosx-version-min=11.0"
        LDFLAGS_EXTRA="-arch $arch -mmacosx-version-min=11.0"
        CMAKE_ARGS+=(-DCMAKE_OSX_ARCHITECTURES="$arch" -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0)
        FF_ARGS+=(--enable-pthreads --arch="$arch")
        if [ "$arch" != "$(uname -m)" ]; then
            FF_ARGS+=(--enable-cross-compile --target-os=darwin)
        fi
        # Every macOS install ships libz, so link the system copy
        BUILD_ZLIB=0
        ;;
    windows-x86_64)
        EXE=ffmpeg.exe
        CROSS=x86_64-w64-mingw32
        # Prefer the win32 thread model so nothing pulls in libwinpthread
        cc="$CROSS-gcc"
        cxx="$CROSS-g++"
        command -v "$CROSS-gcc-win32" >/dev/null && cc="$CROSS-gcc-win32"
        command -v "$CROSS-g++-win32" >/dev/null && cxx="$CROSS-g++-win32"
        LDFLAGS_EXTRA="-static -static-libgcc -static-libstdc++"
        CMAKE_ARGS+=(-DCMAKE_SYSTEM_NAME=Windows -DCMAKE_C_COMPILER="$cc" -DCMAKE_CXX_COMPILER="$cxx"
            -DCMAKE_RC_COMPILER="$CROSS-windres")
        FF_ARGS+=(--enable-w32threads --enable-cross-compile --target-os=mingw32 --arch=x86_64
            --cross-prefix="$CROSS-" --cc="$cc" --cxx="$cxx")
        ;;
    *)
        echo "unknown target: $TARGET" >&2
        exit 2
        ;;
esac

"$ROOT/build/fetch-sources.sh"

rm -rf "$WORK" "$OUT"
mkdir -p "$WORK" "$PREFIX" "$OUT/licenses"
OUT="$(cd "$OUT" && pwd)"
cp -R "$ROOT/sources/ffmpeg" "$ROOT/sources/fdk-aac" "$WORK/"

# --- zlib -------------------------------------------------------------------
if [ "$BUILD_ZLIB" = 1 ]; then
    tar -xzf "$ROOT/sources/zlib-$ZLIB_VERSION.tar.gz" -C "$WORK"
    (
        cd "$WORK/zlib-$ZLIB_VERSION"
        if [ -n "$CROSS" ]; then
            make -f win32/Makefile.gcc -j"$JOBS" PREFIX="$CROSS-" BINARY_PATH="$PREFIX/bin" \
                INCLUDE_PATH="$PREFIX/include" LIBRARY_PATH="$PREFIX/lib" install
        else
            ./configure --static --prefix="$PREFIX"
            make -j"$JOBS" install
        fi
    )
    cp "$WORK/zlib-$ZLIB_VERSION/LICENSE" "$OUT/licenses/zlib-LICENSE.txt"
fi

# --- fdk-aac ----------------------------------------------------------------
cmake -S "$WORK/fdk-aac" -B "$WORK/fdk-aac/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DBUILD_PROGRAMS=OFF \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    ${CMAKE_ARGS[@]+"${CMAKE_ARGS[@]}"}
cmake --build "$WORK/fdk-aac/build" -j"$JOBS"
cmake --install "$WORK/fdk-aac/build"
cp "$WORK/fdk-aac/NOTICE" "$OUT/licenses/fdk-aac-NOTICE.txt"

# --- ffmpeg -----------------------------------------------------------------
LEAN_FLAGS=()
while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -n "$line" ] && LEAN_FLAGS+=("$line")
done <"$ROOT/configure-flags.txt"

(
    cd "$WORK/ffmpeg"
    # Only our prefix: never let configure pick up a host library by accident
    PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig" ./configure \
        --prefix="$PREFIX" \
        --enable-static \
        --disable-shared \
        --pkg-config-flags=--static \
        --extra-version="achew-$PACKAGE_VERSION" \
        --extra-cflags="-I$PREFIX/include $CFLAGS_EXTRA" \
        --extra-cxxflags="$CFLAGS_EXTRA" \
        --extra-ldflags="-L$PREFIX/lib $LDFLAGS_EXTRA" \
        "${LEAN_FLAGS[@]}" \
        ${FF_ARGS[@]+"${FF_ARGS[@]}"} | tee "$WORK/configure.log"
)

license="$(sed -n 's/^License: //p' "$WORK/configure.log")"
if [ "$license" != "LGPL version 2.1 or later" ]; then
    echo "refusing to build: configure reports license '$license'" >&2
    exit 1
fi

# configure only warns when a whole --enable-<type>= list matches nothing, so a
# misspelled name inside a list would be dropped silently. Require every
# explicitly named component to be enabled.
missing=()
for flag in "${LEAN_FLAGS[@]}"; do
    case "$flag" in
        --enable-decoder=* | --enable-encoder=* | --enable-muxer=* | --enable-demuxer=* | \
            --enable-parser=* | --enable-bsf=* | --enable-filter=* | --enable-protocol=*) ;;
        *) continue ;;
    esac
    type="${flag#--enable-}"
    type="${type%%=*}"
    IFS=, read -r -a names <<<"${flag#*=}"
    for name in "${names[@]}"; do
        case "$name" in *'*'*) continue ;; esac
        macro="CONFIG_$(printf '%s_%s' "$name" "$type" | tr '[:lower:]' '[:upper:]')"
        grep -q "^#define $macro 1" "$WORK/ffmpeg/config_components.h" || missing+=("$name ($type)")
    done
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "components requested in configure-flags.txt but not enabled: ${missing[*]}" >&2
    exit 1
fi

make -C "$WORK/ffmpeg" -j"$JOBS"
cp "$WORK/ffmpeg/$EXE" "$OUT/$EXE"
cp "$WORK/ffmpeg/COPYING.LGPLv2.1" "$OUT/licenses/FFmpeg-COPYING.LGPLv2.1.txt"
cp "$WORK/ffmpeg/LICENSE.md" "$OUT/licenses/FFmpeg-LICENSE.md"

# Static runtimes linked into the binary
case "$TARGET" in
    linux-*)
        # Alpine ships no musl license file; this copy matches the musl 1.2.5 in alpine:3.22
        cp "$ROOT/third_party/licenses/musl-COPYRIGHT.txt" "$OUT/licenses/musl-COPYRIGHT.txt"
        ;;
    windows-*)
        cp /usr/share/doc/mingw-w64-common/copyright "$OUT/licenses/mingw-w64-copyright.txt"
        ;;
esac

configuration="$(sed -n 's/^#define FFMPEG_CONFIGURATION "\(.*\)"$/\1/p' "$WORK/ffmpeg/config.h")"
cat >"$OUT/build.json" <<EOF
{
  "target": "$TARGET",
  "package_version": "$PACKAGE_VERSION",
  "ffmpeg_version": "$FFMPEG_VERSION_STRING",
  "ffmpeg_commit": "$FFMPEG_COMMIT",
  "fdk_aac_version": "$FDK_AAC_VERSION",
  "fdk_aac_commit": "$FDK_AAC_COMMIT",
  "zlib_version": "$([ "$BUILD_ZLIB" = 1 ] && echo "$ZLIB_VERSION" || echo system)",
  "license": "$license",
  "configuration": "$configuration"
}
EOF

echo "built $OUT/$EXE ($(du -h "$OUT/$EXE" | cut -f1))"
