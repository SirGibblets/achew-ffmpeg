# achew-ffmpeg

Lean, audio-only builds of [FFmpeg](https://ffmpeg.org) with the Fraunhofer
[fdk-aac](https://github.com/mstorsjo/fdk-aac) decoder, packaged as Python
wheels for [Achew](https://github.com/SirGibblets/achew).

Every Achew install (Docker, `run.sh`, `run.bat`, `dev.sh`) gets this exact
ffmpeg through `uv sync`, so users don't need to install ffmpeg themselves
and everyone runs the same build.

## What's inside

| | |
|---|---|
| FFmpeg | `release/9.0` branch, pinned in [`versions.env`](versions.env) (carries the libfdk-aacdec seek and delay fixes that landed after n9.0.2) |
| fdk-aac | 3.0.0 |
| zlib | 1.3.2, static on Linux and Windows; macOS links the system libz |
| Components | audio demuxers/decoders for the formats Achew accepts, stream-copy muxers, PCM pipes, `silencedetect`, AAC-LC and FLAC encoders. See [`configure-flags.txt`](configure-flags.txt) |
| Binary | `ffmpeg` only (no ffprobe or ffplay), 4–6 MB |

| Target | Wheel platform tag | Built on |
|---|---|---|
| `linux-x86_64` | `manylinux_2_17_x86_64`, `musllinux_1_1_x86_64` | static musl (Alpine), runs on any distro |
| `linux-aarch64` | `manylinux_2_17_aarch64`, `musllinux_1_1_aarch64` | static musl (Alpine) |
| `macos-arm64` | `macosx_11_0_arm64` | Apple Silicon |
| `macos-x86_64` | `macosx_11_0_x86_64` | cross-compiled on Apple Silicon |
| `windows-x86_64` | `win_amd64` | mingw-w64 cross, static |

## Using it

Each [release](https://github.com/SirGibblets/achew-ffmpeg/releases) has a
ready-to-paste `[tool.uv.sources]` block that pins one wheel URL per platform;
`uv lock` then records each wheel's hash. The wheel installs `ffmpeg` into the
environment's `bin/` (`Scripts\` on Windows), which `uv run` puts first on
PATH, so libraries that call `ffmpeg` by name (mlx-whisper, parakeet-mlx) use
it too.

```python
import achew_ffmpeg

achew_ffmpeg.ffmpeg_path()  # absolute path to the bundled binary
achew_ffmpeg.build_info()   # versions, commits, configure line
```

`python -m achew_ffmpeg` prints the same information, which is handy for bug
reports.

FFmpeg still prefers its native `aac` decoder, so for xHE-AAC input pass
libfdk_aac explicitly, and turn off fdk's default loudness normalization so
levels match other codecs:

```sh
ffmpeg -c:a libfdk_aac -drc_level -2 -i book.m4b ...
```

Seeking is sample-accurate. It isn't always bit-exact, though: USAC noise
filling and SBR synthesize noise from a generator whose state depends on where
decoding started.

## Building

```sh
build/build.sh macos-arm64                       # on a Mac
build/build.sh macos-x86_64                      # on a Mac, needs nasm
docker build -f build/linux.Dockerfile --platform linux/arm64 \
  --build-arg TARGET=linux-aarch64 --output out/linux-aarch64 .
docker build -f build/linux.Dockerfile --platform linux/amd64 \
  --build-arg TARGET=linux-x86_64 --output out/linux-x86_64 .
docker build -f build/windows.Dockerfile --output out/windows-x86_64 .

python3 packaging/build_wheel.py out/<target> dist/wheels
```

`build/fetch-sources.sh` fetches the pinned sources and checks them against
`versions.env`. The build fails if configure reports any license other than
LGPL, or if a component named in `configure-flags.txt` isn't actually enabled.

## Testing

```sh
tests/make_fixtures.sh                     # needs a full ffmpeg (Homebrew, apt)
python3 tests/smoke.py --ffmpeg out/<target>/ffmpeg
```

[`tests/smoke.py`](tests/smoke.py) replays the ffmpeg commands Achew and its
ASR libraries run against one fixture per supported container. Fixtures include
cover art and chapters, plus the committed
[`xhe-aac-speech.m4a`](tests/fixtures/xhe-aac-speech.m4a): synthetic speech
that exercises USAC's speech mode. Regenerate it with
`docker build -f tools/xhe-fixture/Dockerfile --output tests/fixtures .`.

CI builds every target, then installs each wheel on its native OS (and the
Linux wheels on Alpine) and runs the smoke tests.

## Releasing

1. Update [`versions.env`](versions.env) and bump `PACKAGE_VERSION`
   (`<FFmpeg version>.post<n>`).
2. Push a tag `v<PACKAGE_VERSION>`. CI builds, tests, and publishes the wheels,
   the complete corresponding sources and `SHA256SUMS` as a GitHub release.

Never replace a release asset: consumers pin each wheel's hash in `uv.lock`.
Publish a new version instead.

## Licensing

FFmpeg can only be redistributed with libfdk_aac when it's built **without**
`--enable-gpl` (and without `--enable-nonfree`), so these builds are LGPL and
contain only the components Achew uses.

- The build scripts and the `achew_ffmpeg` helper are [MIT](LICENSE).
- The ffmpeg binaries are **LGPL-2.1-or-later** and statically include:
  - **fdk-aac**, under the [Fraunhofer FDK AAC license](https://github.com/mstorsjo/fdk-aac/blob/master/NOTICE)
  - **zlib** (Linux, Windows), under the zlib license
  - the **musl** C library (Linux), under the MIT license
  - the **mingw-w64** runtime (Windows)

  Each wheel ships these license texts in `.dist-info/licenses/`. Each release
  attaches the exact FFmpeg, fdk-aac and zlib sources the binaries were built
  from.
- The FDK AAC license **grants no patent rights**. AAC and xHE-AAC are covered
  by patents licensed through [Via LA](https://www.via-la.com); FFmpeg's own
  AAC decoders carry the same exposure. This is not legal advice.
