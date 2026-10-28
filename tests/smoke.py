"""Replay the ffmpeg commands achew and its ASR libraries run, against every fixture.

usage: python3 tests/smoke.py [--ffmpeg PATH] [FIXTURE_DIR ...]

Defaults to the binary from the installed achew-ffmpeg wheel (falling back to
out/<host target>/ffmpeg) and to tests/fixtures plus tests/fixtures/generated.
Stdlib only, so it runs on bare CI runners. Exits non-zero on any failure.

The command shapes mirror achew's backend/app/services (audio_service.py,
processing_pipeline.py, vad_worker.py, parakeet_service.py) and the loaders in
mlx_whisper and parakeet_mlx. Update them together.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import re
import subprocess
import sys
import tempfile
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

# achew's segment-extension rules (audio_service.pick/probe_segment_extension)
REMUX_EXTS = {"m4b", "m4a", "mp4"}
PROBE_CANDIDATES = ["aac", "m4a"]
CODEC_EXT = {
    "mp3": "mp3",
    "aac": "aac",
    "alac": "m4a",
    "flac": "flac",
    "opus": "opus",
    "vorbis": "ogg",
    "ac3": "ac3",
    "eac3": "eac3",
}

AUDIO_STREAM_RE = re.compile(r"Stream #\d+[:.\d\[\]\w()]*: Audio:\s+([\w\-]+)")
DURATION_RE = re.compile(r"Duration: (\d+):(\d+):([\d.]+)")
STREAM_FORMAT_RE = re.compile(r"Audio: .*?, (\d+) Hz, (mono|stereo)")

# Defaults for fixtures without an entry in fixtures/expected.json
DEFAULT_EXPECTED = {"silences": 2, "input_args": []}


class Runner:
    def __init__(self, ffmpeg: str):
        self.ffmpeg = ffmpeg

    def __call__(self, *args: str, stdin: bytes | None = None) -> tuple[int, bytes, str]:
        proc = subprocess.run([self.ffmpeg, *args], check=False, input=stdin, capture_output=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", errors="replace")


def default_ffmpeg() -> str:
    try:
        import achew_ffmpeg

        return achew_ffmpeg.ffmpeg_path()
    except ImportError:
        pass
    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}[platform.system()]
    machine = {"amd64": "x86_64", "arm64": "arm64" if system == "macos" else "aarch64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    return str(ROOT / "out" / f"{system}-{machine}" / ("ffmpeg.exe" if system == "windows" else "ffmpeg"))


def snr_db(ref: array, got: array) -> float:
    signal = sum(x * x for x in ref)
    error = sum((a - b) ** 2 for a, b in zip(ref, got))
    return 10 * math.log10(signal / error) if error and signal else math.inf


def check_binary(ff: Runner) -> list[tuple[str, bool, str]]:
    results = []
    rc, out, _ = ff("-hide_banner", "-L")
    text = out.decode(errors="replace")
    results.append(
        ("license is LGPL", rc == 0 and "GNU Lesser General Public" in text, text.splitlines()[0] if text else "")
    )
    rc, out, _ = ff("-hide_banner", "-buildconf")
    conf = out.decode(errors="replace")
    bad = [flag for flag in ("--enable-gpl", "--enable-nonfree", "--enable-version3") if flag in conf]
    results.append(("no gpl/nonfree flags", rc == 0 and not bad, ", ".join(bad)))
    rc, out, _ = ff("-hide_banner", "-decoders")
    results.append(("libfdk_aac decoder present", rc == 0 and b" libfdk_aac " in out, ""))
    return results


def segment_extension(ff: Runner, fixture: Path, input_args: list[str], tmp: Path) -> str:
    src_ext = fixture.suffix.lstrip(".").lower()
    if src_ext not in REMUX_EXTS:
        return src_ext
    _, _, err = ff("-hide_banner", *input_args, "-i", str(fixture))
    match = AUDIO_STREAM_RE.search(err)
    codec = match.group(1) if match else None
    candidates = list(PROBE_CANDIDATES)
    if codec in CODEC_EXT and CODEC_EXT[codec] not in candidates:
        candidates.insert(0, CODEC_EXT[codec])
    for ext in candidates:
        rc, _, _ = ff(
            "-y", "-hide_banner", "-loglevel", "error", "-ss", "0", "-t", "2.0", *input_args,
            "-i", str(fixture), "-map", "0:a:0", "-acodec", "copy",
            "-f", "segment", "-segment_times", "1.0", str(tmp / f"probe_%03d.{ext}"),
        )  # fmt: skip
        if rc == 0:
            return ext
    return "wav"


def check_fixture(ff: Runner, fixture: Path, expected: dict) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    inp = list(expected.get("input_args", []))
    name = fixture.name

    def check(label: str, ok: bool, detail: str = "") -> None:
        results.append((f"{name}: {label}", ok, detail))

    _, _, err = ff("-hide_banner", *inp, "-i", str(fixture))
    stream = AUDIO_STREAM_RE.search(err)
    dur_m = DURATION_RE.search(err)
    fmt_m = STREAM_FORMAT_RE.search(err)
    check("probe finds audio stream", bool(stream and dur_m), stream.group(1) if stream else err[-200:])
    if not (stream and dur_m):
        return results
    duration = int(dur_m.group(1)) * 3600 + int(dur_m.group(2)) * 60 + float(dur_m.group(3))
    # Raw ADTS has no duration field, and ffmpeg's bitrate-based estimate can be
    # far off, so only require a non-empty decode there
    estimated = "Estimating duration from bitrate" in err

    def near(actual: float, target: float) -> bool:
        return actual > 0 if estimated else abs(actual - target) <= 0.03 * target

    # Full-file silence detection (audio_service._sync_get_silence_boundaries)
    rc, _, err = ff("-nostdin", *inp, "-i", str(fixture), "-af", "silencedetect=n=-30dB:d=1.0", "-f", "null", "-")
    count = err.count("silence_end")
    errors = [line for line in err.splitlines() if "rror" in line]
    check("silencedetect", rc == 0 and count == expected["silences"] and not errors,
          f"rc={rc} silences={count} expected={expected['silences']} errors={len(errors)}")  # fmt: skip

    # Parallel silence worker shape (audio_service, one virtual segment)
    rc, _, err = ff("-ss", "1", *inp, "-i", str(fixture), "-t", "8", "-vn",
                    "-af", "silencedetect=n=-30dB:d=1.0", "-f", "null", "-")  # fmt: skip
    check("seeked silence worker", rc == 0, err[-200:] if rc else "")

    # VAD worker / Parakeet (ONNX) loader: 16 kHz mono f32le on stdout
    rc, out, err = ff(
        "-loglevel", "error", "-y", *inp, "-i", str(fixture), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"
    )
    check("f32le 16k pipe", rc == 0 and near(len(out) / 4 / 16000, duration), f"rc={rc} seconds={len(out) / 64000:.2f}")

    # mlx_whisper / parakeet_mlx loaders: 16 kHz mono s16le on stdout
    rc, out, err = ff("-nostdin", *inp, "-i", str(fixture), "-threads", "0", "-f", "s16le", "-ac", "1",
                      "-acodec", "pcm_s16le", "-ar", "16000", "-")  # fmt: skip
    check("s16le 16k pipe (mlx loaders)", rc == 0 and near(len(out) / 2 / 16000, duration),
          f"rc={rc} seconds={len(out) / 32000:.2f}")  # fmt: skip

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        ext = segment_extension(ff, fixture, inp, tmp)
        check(f"segment extension probe -> {ext}", ext != "wav" or fixture.suffix == ".wav")

        # Single chapter extraction with stream copy (audio_service._extract_single_segment)
        seg = tmp / f"segment.{ext}"
        rc, _, err = ff(
            "-y", "-ss", "2", "-t", "4", *inp, "-i", str(fixture), "-map", "0:a:0", "-acodec", "copy", str(seg)
        )
        check("stream-copy extraction", rc == 0 and seg.exists(), err[-200:] if rc else "")

        # The copied segment must decode and trim (_sync_get_silence_boundaries, _trim_segment)
        rc, _, err = ff(*inp, "-i", str(seg), "-af", "silencedetect=n=-30dB:d=1.0", "-f", "null", "-")
        check("decode copied segment", rc == 0 and "rror" not in err, err[-200:] if rc else "")
        trimmed = tmp / f"trimmed.{ext}"
        rc, _, err = ff("-y", *inp, "-i", str(seg), "-t", "1.5", "-c", "copy", str(trimmed))
        check("stream-copy trim", rc == 0, err[-200:] if rc else "")

        # ASR snippet as WAV (extension "wav" disables copy)
        wav = tmp / "snippet.wav"
        rc, _, err = ff("-y", "-ss", "2", "-t", "3", *inp, "-i", str(fixture), "-map", "0:a:0", str(wav))
        check("wav extraction", rc == 0 and wav.exists() and wav.stat().st_size > 100_000, err[-200:] if rc else "")

        # Segmented range block, 16 kHz mono (audio_service._extract_range_block, non-copy path)
        rc, _, err = ff("-y", "-ss", "1", "-t", "8", *inp, "-i", str(fixture), "-map", "0:a:0", "-ac", "1", "-ar", "16000",
                        "-f", "segment", "-segment_times", "3,6", str(tmp / "block_%03d.wav"))  # fmt: skip
        check("segmented range block", rc == 0 and len(list(tmp.glob("block_*.wav"))) == 3, err[-200:] if rc else "")

        # Segmented stream copy (processing_pipeline._run_split_extraction)
        rc, _, err = ff("-y", "-ss", "1", "-to", "9", *inp, "-i", str(fixture), "-map", "0:a:0", "-c", "copy",
                        "-f", "segment", "-segment_times", "3,6", str(tmp / f"split_%03d.{ext}"))  # fmt: skip
        check("segmented stream copy", rc == 0 and len(list(tmp.glob(f"split_*.{ext}"))) >= 2, err[-200:] if rc else "")

        # Jitter crop copies every default stream, cover art included (audio_service crop)
        crop = tmp / f"jitter{fixture.suffix}"
        rc, _, err = ff("-y", "-ss", "0.5", *inp, "-i", str(fixture), "-c", "copy", str(crop))
        check("jitter crop (all streams)", rc == 0, err[-300:] if rc else "")

        # Multi-file books: concat demuxer with stream copy (audio_service.concat_files)
        listing = tmp / "concat.txt"
        listing.write_text(f"file '{fixture}'\nfile '{fixture}'\n", encoding="utf-8")
        joined = tmp / f"concatenated.{ext}"
        rc, _, err = ff(
            "-y", "-f", "concat", "-safe", "0", *inp, "-i", str(listing), "-map", "0:a:0", "-c", "copy", str(joined)
        )
        check("concat demuxer copy", rc == 0 and joined.exists(), err[-300:] if rc else "")

        # Browser playback fallback: AAC-LC transcode of a window
        play = tmp / "playback.m4a"
        rc, _, err = ff(
            "-y",
            "-ss",
            "2",
            *inp,
            "-i",
            str(fixture),
            "-t",
            "5",
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(play),
        )
        check("aac playback transcode", rc == 0, err[-200:] if rc else "")

    if expected.get("native_decoder_fails"):
        # Proves the fixture still exercises speech-mode (LPD) frames
        _, _, err = ff("-nostdin", "-c:a", "aac", "-i", str(fixture), "-f", "null", "-")
        check("native aac decoder rejects LPD frames", "Not yet implemented" in err)

    if expected.get("seek_points") and fmt_m:
        # A direct seek must line up sample-for-sample with a decode started
        # earlier and be clean from its first frame. It needn't be bit-exact:
        # USAC noise filling and SBR synthesize noise from a generator whose
        # state depends on where decoding started, so those bands differ.
        rate, channels = int(fmt_m.group(1)), 1 if fmt_m.group(2) == "mono" else 2
        head = round(0.1 * rate) * channels
        for t in expected["seek_points"]:
            pre = min(5.0, t)
            _, direct_b, _ = ff("-ss", str(t), *inp, "-i", str(fixture), "-t", "2", "-map", "0:a:0", "-f", "f32le", "-")
            _, ref_b, _ = ff(
                "-ss", str(t - pre), *inp, "-i", str(fixture), "-t", str(pre + 2), "-map", "0:a:0", "-f", "f32le", "-"
            )
            direct = array("f", direct_b)
            ref = array("f", ref_b)[round(pre * rate) * channels :][: len(direct)]
            whole, first = snr_db(ref, direct), snr_db(ref[:head], direct[:head])
            check(f"seek to {t}s aligned and clean", len(direct) == len(ref) > 0 and whole >= 20 and first >= 15,
                  f"snr={whole:.1f} dB, first 100 ms={first:.1f} dB")  # fmt: skip

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="list passing checks too")
    parser.add_argument("dirs", nargs="*", type=Path)
    args = parser.parse_args()

    ffmpeg = args.ffmpeg or default_ffmpeg()
    dirs = args.dirs or [FIXTURES, FIXTURES / "generated"]
    ff = Runner(ffmpeg)
    print(f"ffmpeg: {ffmpeg}")
    _, out, _ = ff("-hide_banner", "-version")
    print(out.decode(errors="replace").splitlines()[0])

    expected_all = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    fixtures = sorted(p for d in dirs if d.is_dir() for p in d.iterdir() if p.is_file() and p.name != "expected.json")
    if not fixtures:
        print("no fixtures found; run tests/make_fixtures.sh first", file=sys.stderr)
        return 1

    results = check_binary(ff)
    for fixture in fixtures:
        results += check_fixture(ff, fixture, {**DEFAULT_EXPECTED, **expected_all.get(fixture.name, {})})

    failed = [r for r in results if not r[1]]
    for label, ok, detail in results:
        if not ok or args.verbose:
            print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed across {len(fixtures)} fixtures")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
