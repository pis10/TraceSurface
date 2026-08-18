from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from playwright._impl._driver import compute_driver_executable, get_driver_env
from playwright.sync_api import sync_playwright

from tracesurface.storage.sqlite.connection import get_home


def system_chrome_path() -> Path | None:
    if sys.platform == "darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home()
            / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
        names = ("google-chrome",)
    elif sys.platform == "win32":
        candidates = [
            Path(root) / "Google/Chrome/Application/chrome.exe"
            for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if (root := os.environ.get(key))
        ]
        names = ("chrome", "google-chrome")
    else:
        candidates = [Path("/opt/google/chrome/chrome")]
        names = ("google-chrome-stable", "google-chrome")

    candidates.extend(Path(path) for name in names if (path := shutil.which(name)))
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def configure_browser_path() -> Path:
    path = get_home() / "browsers"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(path)
    return path


def chromium_is_installed() -> bool:
    configure_browser_path()
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
    return executable.is_file()


def install_chromium() -> None:
    configure_browser_path().mkdir(exist_ok=True)
    driver, cli = compute_driver_executable()
    subprocess.run(
        [driver, cli, "install", "--no-shell", "chromium"],
        env=get_driver_env(),
        check=True,
    )
