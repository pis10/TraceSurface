from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from tracesurface import __version__

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "build" / "nuitka"


def _check_platform() -> None:
    machine = platform.machine().lower()
    supported = {
        "win32": {"amd64", "x86_64"},
        "darwin": {"arm64", "aarch64"},
        "linux": {"amd64", "x86_64"},
    }
    if sys.platform not in supported or machine not in supported[sys.platform]:
        raise SystemExit(f"Unsupported build target: {sys.platform} {machine}")


def main() -> None:
    _check_platform()
    if not (ROOT / "tracesurface/server/static/index.html").is_file():
        raise SystemExit("Frontend is missing. Run `npm run build` in frontend first.")

    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    OUTPUT_DIR.mkdir(parents=True)

    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--mode=onefile",
        "--python-flag=-m",
        "--assume-yes-for-downloads",
        "--playwright-include-browser=none",
        f"--onefile-tempdir-spec={{CACHE_DIR}}/TraceSurface/{__version__}",
        f"--output-dir={OUTPUT_DIR}",
        "--output-filename=tracesurface",
        "--include-data-dir=tracesurface/server/static=tracesurface/server/static",
        "--include-data-dir=tracesurface/storage/sqlite/migrations=tracesurface/storage/sqlite/migrations",
        "--include-data-files=tracesurface/secrets/rules.yml=tracesurface/secrets/rules.yml",
    ]
    if sys.platform == "win32":
        command.append("--msvc=latest")
    command.append("tracesurface")

    subprocess.run(command, cwd=ROOT, check=True)
    binary = OUTPUT_DIR / ("tracesurface.exe" if sys.platform == "win32" else "tracesurface")
    if not binary.is_file():
        raise SystemExit(f"Build finished but binary was not found: {binary}")
    print(binary)


if __name__ == "__main__":
    main()
