from __future__ import annotations

import unittest

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tracesurface.collection.runtime.cdp_trace import (
    CDPCollectRequest,
    CDPTraceSession,
)


class _CDPClient:
    def __init__(self) -> None:
        self.handlers = {}
        self.detached = False

    async def send(self, method: str, params=None):
        del method, params
        return {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    async def detach(self) -> None:
        self.detached = True


class _Context:
    def __init__(self, client: _CDPClient) -> None:
        self.client = client

    async def new_cdp_session(self, page):
        del page
        return self.client


class _Request:
    resource_type = "script"


class _Response:
    request = _Request()
    status = 200
    url = "https://example.test/app.js"

    async def text(self) -> str:
        return "fetch('/api/users')"


class _Page:
    def __init__(
        self,
        *,
        goto_timeout: bool = False,
        networkidle_timeout: bool = False,
    ) -> None:
        self.client = _CDPClient()
        self.context = _Context(self.client)
        self.goto_timeout = goto_timeout
        self.networkidle_timeout = networkidle_timeout
        self.listeners = {}
        self.goto_call = None
        self.wait_call = None

    def on(self, event: str, handler) -> None:
        self.listeners[event] = handler

    def remove_listener(self, event: str, handler) -> None:
        if self.listeners.get(event) is handler:
            self.listeners.pop(event)

    async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_call = (url, wait_until, timeout)
        if self.goto_timeout:
            raise PlaywrightTimeoutError("domcontentloaded timeout")
        self.listeners["response"](_Response())

    async def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        self.wait_call = (state, timeout)
        if self.networkidle_timeout:
            raise PlaywrightTimeoutError("networkidle timeout")

    async def content(self) -> str:
        return "<html></html>"


class CDPTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_domcontentloaded_then_networkidle_and_reuses_script_body(
        self,
    ) -> None:
        page = _Page()

        result = await CDPTraceSession().collect(
            page,  # type: ignore[arg-type]
            CDPCollectRequest(
                target_url="https://example.test/",
                wait_ms=7000,
                goto_timeout_ms=10000,
            ),
        )

        self.assertEqual(
            page.goto_call,
            ("https://example.test/", "domcontentloaded", 10000),
        )
        self.assertEqual(page.wait_call, ("networkidle", 7000))
        self.assertEqual(
            result.js_sources,
            {"https://example.test/app.js": "fetch('/api/users')"},
        )
        self.assertTrue(page.client.detached)

    async def test_networkidle_timeout_keeps_captured_data(self) -> None:
        page = _Page(networkidle_timeout=True)

        result = await CDPTraceSession().collect(
            page,  # type: ignore[arg-type]
            CDPCollectRequest(
                target_url="https://example.test/",
                wait_ms=50,
                goto_timeout_ms=10000,
            ),
        )

        self.assertEqual(result.html_content, "<html></html>")
        self.assertIn("https://example.test/app.js", result.js_sources)

    async def test_domcontentloaded_timeout_is_a_hard_failure(self) -> None:
        page = _Page(goto_timeout=True)

        with self.assertRaises(PlaywrightTimeoutError):
            await CDPTraceSession().collect(
                page,  # type: ignore[arg-type]
                CDPCollectRequest(
                    target_url="https://example.test/",
                    wait_ms=7000,
                    goto_timeout_ms=10000,
                ),
            )

        self.assertIsNone(page.wait_call)
        self.assertTrue(page.client.detached)
