"""Assemble a platform wheel around one prebuilt ffmpeg binary.

usage: python3 packaging/build_wheel.py <build dir> <out dir>

<build dir> is the output of build/build.sh: the binary, licenses/ and
build.json. The binary goes in the wheel's .data/scripts, so installers put it
in the environment's bin/Scripts directory with its executable bit set. The
license texts go in .dist-info/licenses. Output is byte-for-byte reproducible
for the same inputs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "achew-ffmpeg"
DIST = "achew_ffmpeg"

# A fully static binary only needs the kernel, so it can claim any libc floor.
# manylinux_2_17 is the oldest tag current installers still treat as a baseline.
PLATFORM_TAGS = {
    "linux-x86_64": ["manylinux_2_17_x86_64", "manylinux2014_x86_64", "musllinux_1_1_x86_64"],
    "linux-aarch64": ["manylinux_2_17_aarch64", "manylinux2014_aarch64", "musllinux_1_1_aarch64"],
    "macos-arm64": ["macosx_11_0_arm64"],
    "macos-x86_64": ["macosx_11_0_x86_64"],
    "windows-x86_64": ["win_amd64"],
}

# PEP 508 marker selecting each target's wheel in a consumer's [tool.uv.sources]
PLATFORM_MARKERS = {
    "linux-x86_64": "sys_platform == 'linux' and platform_machine == 'x86_64'",
    "linux-aarch64": "sys_platform == 'linux' and platform_machine == 'aarch64'",
    "macos-arm64": "sys_platform == 'darwin' and platform_machine == 'arm64'",
    "macos-x86_64": "sys_platform == 'darwin' and platform_machine == 'x86_64'",
    "windows-x86_64": "sys_platform == 'win32' and platform_machine == 'AMD64'",
}

# SPDX expression per license file that build.sh may emit
LICENSE_SPDX = {
    "FFmpeg-COPYING.LGPLv2.1.txt": "LGPL-2.1-or-later",
    "fdk-aac-NOTICE.txt": "FDK-AAC",
    "zlib-LICENSE.txt": "Zlib",
    "musl-COPYRIGHT.txt": "MIT",
    "mingw-w64-copyright.txt": "LicenseRef-mingw-w64-runtime",
}

# Fixed timestamp so rebuilding the same inputs yields an identical wheel
ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}"


def build(build_dir: Path, out_dir: Path) -> Path:
    info = json.loads((build_dir / "build.json").read_text(encoding="utf-8"))
    target = info["target"]
    version = read_env(ROOT / "versions.env")["PACKAGE_VERSION"]
    if info["package_version"] != version:
        raise SystemExit(f"{build_dir} was built as {info['package_version']}, versions.env says {version}")

    tags = PLATFORM_TAGS[target]
    exe = "ffmpeg.exe" if target.startswith("windows-") else "ffmpeg"
    data_dir = f"{DIST}-{version}.data"
    dist_info = f"{DIST}-{version}.dist-info"

    licenses = sorted((build_dir / "licenses").iterdir())
    license_files = [("achew-ffmpeg-LICENSE.txt", (ROOT / "LICENSE").read_bytes())]
    license_files += [(p.name, p.read_bytes()) for p in licenses]
    spdx = ["MIT"] + [LICENSE_SPDX[p.name] for p in licenses if p.name in LICENSE_SPDX]
    expression = " AND ".join(dict.fromkeys(spdx))

    metadata = "\n".join(
        [
            "Metadata-Version: 2.4",
            f"Name: {NAME}",
            f"Version: {version}",
            "Summary: Lean, audio-only LGPL FFmpeg build with libfdk_aac (xHE-AAC decoding) for achew",
            f"License-Expression: {expression}",
            *[f"License-File: {name}" for name, _ in license_files],
            "Project-URL: Source, https://github.com/SirGibblets/achew-ffmpeg",
            "Requires-Python: >=3.10",
            "Description-Content-Type: text/markdown",
            "",
            f"FFmpeg {info['ffmpeg_version']} with fdk-aac {info['fdk_aac_version']} for `{target}`.",
            "See https://github.com/SirGibblets/achew-ffmpeg for sources and build scripts.",
            "",
        ]
    )
    wheel = "\n".join(
        [
            "Wheel-Version: 1.0",
            "Generator: achew-ffmpeg build_wheel.py",
            "Root-Is-Purelib: false",
            *[f"Tag: py3-none-{tag}" for tag in tags],
            "",
        ]
    )

    pkg = ROOT / "packaging" / DIST
    entries: list[tuple[str, bytes, int]] = [
        (f"{DIST}/__init__.py", (pkg / "__init__.py").read_bytes(), 0o644),
        (f"{DIST}/__main__.py", (pkg / "__main__.py").read_bytes(), 0o644),
        (f"{DIST}/build.json", (build_dir / "build.json").read_bytes(), 0o644),
        (f"{data_dir}/scripts/{exe}", (build_dir / exe).read_bytes(), 0o755),
        *[(f"{dist_info}/licenses/{name}", data, 0o644) for name, data in license_files],
        (f"{dist_info}/METADATA", metadata.encode(), 0o644),
        (f"{dist_info}/WHEEL", wheel.encode(), 0o644),
    ]
    record = "".join(f"{path},{record_hash(data)},{len(data)}\n" for path, data, _ in entries)
    record += f"{dist_info}/RECORD,,\n"
    entries.append((f"{dist_info}/RECORD", record.encode(), 0o644))

    out_dir.mkdir(parents=True, exist_ok=True)
    wheel_path = out_dir / f"{DIST}-{version}-py3-none-{'.'.join(tags)}.whl"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        for path, data, mode in entries:
            zi = zipfile.ZipInfo(path, date_time=ZIP_DATE)
            zi.external_attr = (0o100000 | mode) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, data, compresslevel=9)
    return wheel_path


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(build(Path(sys.argv[1]), Path(sys.argv[2])))
