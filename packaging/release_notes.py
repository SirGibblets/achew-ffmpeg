"""Write release notes, including the [tool.uv.sources] block consumers paste in.

usage: python3 packaging/release_notes.py <wheel dir> <release tag>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_wheel import PLATFORM_MARKERS, PLATFORM_TAGS, ROOT, read_env

REPO = "https://github.com/SirGibblets/achew-ffmpeg"


def target_for(wheel: Path) -> str:
    for target, tags in PLATFORM_TAGS.items():
        if wheel.name.endswith(f"-py3-none-{'.'.join(tags)}.whl"):
            return target
    raise SystemExit(f"unrecognized wheel: {wheel.name}")


def main(wheel_dir: Path, tag: str) -> str:
    env = read_env(ROOT / "versions.env")
    wheels = {target_for(w): w for w in sorted(wheel_dir.glob("*.whl"))}
    missing = sorted(set(PLATFORM_TAGS) - set(wheels))
    if missing:
        raise SystemExit(f"missing wheels for: {', '.join(missing)}")

    any_platform = " or ".join(f"({PLATFORM_MARKERS[t]})" for t in PLATFORM_TAGS)
    sources = "\n".join(
        f'  {{ url = "{REPO}/releases/download/{tag}/{wheels[t].name}", marker = "{PLATFORM_MARKERS[t]}" }},'
        for t in PLATFORM_TAGS
    )
    return f"""\
FFmpeg `{env["FFMPEG_VERSION_STRING"]}` ([{env["FFMPEG_COMMIT"][:10]}](https://github.com/FFmpeg/FFmpeg/commit/{env["FFMPEG_COMMIT"]})) \
with fdk-aac {env["FDK_AAC_VERSION"]}, built as a lean, audio-only LGPL binary for achew.

### Using this release in achew

In `backend/pyproject.toml`:

```toml
dependencies = [
  "achew-ffmpeg=={env["PACKAGE_VERSION"]}; {any_platform}",
]

[tool.uv.sources]
achew-ffmpeg = [
{sources}
]
```

Then run `uv lock`.

### Licensing

The binaries are LGPL-2.1-or-later (FFmpeg) and include the Fraunhofer FDK AAC library under its own license, which grants no patent rights. \
Complete corresponding sources are attached to this release, and every wheel carries the license texts in its `.dist-info/licenses` directory. \
See the README for details.
"""


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(main(Path(sys.argv[1]), sys.argv[2]))
