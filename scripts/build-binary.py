from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "build" / "standalone"


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

    # Ship the tokenizer data so custom model analysis works without an extra
    # download on the user's first request.
    tokenizer_dir = OUTPUT_DIR / "tokenizer-cache"
    subprocess.run(
        [sys.executable, "-c", "import tiktoken; tiktoken.get_encoding('cl100k_base')"],
        env={**os.environ, "TIKTOKEN_CACHE_DIR": str(tokenizer_dir)},
        check=True,
    )
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--name=tracesurface",
        f"--paths={ROOT}",
        f"--distpath={OUTPUT_DIR}",
        f"--workpath={OUTPUT_DIR / 'work'}",
        f"--specpath={OUTPUT_DIR}",
        "--collect-submodules=pydantic_ai",
        "--collect-submodules=tiktoken_ext",
        "--collect-data=genai_prices",
        "--recursive-copy-metadata=pydantic-ai-slim",
        "--copy-metadata=tiktoken",
        "--copy-metadata=openai",
        "--copy-metadata=anthropic",
        "--copy-metadata=google-genai",
        f"--runtime-hook={ROOT / 'scripts' / 'pyinstaller-runtime.py'}",
        f"--add-data={tokenizer_dir}:tiktoken_cache",
        f"--add-data={ROOT / 'tracesurface/server/static'}:tracesurface/server/static",
        f"--add-data={ROOT / 'tracesurface/storage/sqlite/schema.sql'}:tracesurface/storage/sqlite",
        f"--add-data={ROOT / 'tracesurface/secrets/rules.yml'}:tracesurface/secrets",
        str(ROOT / "tracesurface" / "__main__.py"),
    ]

    subprocess.run(command, cwd=ROOT, check=True)
    binary = OUTPUT_DIR / (
        "tracesurface.exe" if sys.platform == "win32" else "tracesurface"
    )
    if not binary.is_file():
        raise SystemExit(f"Build finished but binary was not found: {binary}")
    print(binary)


if __name__ == "__main__":
    main()
