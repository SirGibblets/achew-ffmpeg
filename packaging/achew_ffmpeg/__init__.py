"""Locate the ffmpeg binary installed by the achew-ffmpeg wheel.

The wheel ships ffmpeg as a script, so installers put it in the environment's
scripts directory (``.venv/bin`` or ``.venv\\Scripts``), which ``uv run`` also
puts first on PATH. Use :func:`ffmpeg_path` when you need the exact binary
rather than whatever PATH resolves.
"""

from __future__ import annotations

import json
import os
import sys
import sysconfig
from functools import lru_cache
from pathlib import Path

__all__ = ["build_info", "ffmpeg_path"]

_EXE = "ffmpeg" + (sysconfig.get_config_var("EXE") or "")


def ffmpeg_path() -> str:
    """Return the absolute path of the bundled ffmpeg binary.

    Raises FileNotFoundError if the wheel's binary is missing (for example, a
    broken install or an unsupported platform).
    """
    candidates = [
        Path(sysconfig.get_path("scripts")) / _EXE,
        Path(sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user"))) / _EXE,
        # `pip install --target` puts scripts in a bin/ next to the packages
        Path(__file__).resolve().parent.parent / "bin" / _EXE,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(f"achew-ffmpeg binary not found; looked in: {', '.join(map(str, candidates))}")


@lru_cache(maxsize=1)
def build_info() -> dict:
    """Return the build metadata recorded for this binary (versions, commits, configure line)."""
    return json.loads((Path(__file__).parent / "build.json").read_text(encoding="utf-8"))


def _main() -> int:
    try:
        path = ffmpeg_path()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    info = build_info()
    print(f"ffmpeg:  {path}")
    for key in ("package_version", "ffmpeg_version", "fdk_aac_version", "zlib_version", "target", "license"):
        print(f"{key + ':':<17}{info.get(key)}")
    return 0 if os.access(path, os.X_OK) else 1
