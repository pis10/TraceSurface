from __future__ import annotations

from playwright.async_api import Browser, Playwright

from tracesurface.browser import configure_browser_path, system_chrome_path


async def launch_browser(playwright: Playwright, *, headless: bool = True) -> Browser:
    configure_browser_path()
    chrome = system_chrome_path()
    if chrome is not None:
        return await playwright.chromium.launch(
            headless=headless,
            executable_path=str(chrome),
        )
    return await playwright.chromium.launch(headless=headless, channel="chromium")
