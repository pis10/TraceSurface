from __future__ import annotations

from playwright.async_api import Browser, Playwright

from tracesurface.browser import configure_browser_path


async def launch_browser(playwright: Playwright, *, headless: bool = True) -> Browser:
    configure_browser_path()
    return await playwright.chromium.launch(headless=headless, channel="chromium")
