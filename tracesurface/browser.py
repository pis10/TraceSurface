from __future__ import annotations

import os
import subprocess
from pathlib import Path

from playwright._impl._driver import compute_driver_executable, get_driver_env
from playwright.sync_api import sync_playwright

from tracesurface.storage.sqlite.connection import get_home


def configure_browser_path() -> None:
    path = get_home() / "browsers"
    path.mkdir(exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(path)


def chromium_is_installed() -> bool:
    configure_browser_path()
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
    return executable.is_file()


def install_chromium() -> None:
    configure_browser_path()
    driver, cli = compute_driver_executable()
    subprocess.run(
        [driver, cli, "install", "--no-shell", "chromium"],
        env=get_driver_env(),
        check=True,
    )
