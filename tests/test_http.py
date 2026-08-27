from __future__ import annotations

import asyncio
import unittest

import httpx

from tracesurface.collection.deps import HttpTextClient
from tracesurface.http import StatelessAsyncClient


class HttpClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_stateless_client_does_not_replay_response_cookies(self) -> None:
        cookies: list[str] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            cookies.append(request.headers.get("cookie", ""))
            return httpx.Response(
                200,
                headers={"set-cookie": "session=secret; Path=/"},
                text="ok",
            )

        async with StatelessAsyncClient(
            transport=httpx.MockTransport(handle),
        ) as client:
            await client.get("https://example.test/first")
            await client.get("https://example.test/second")

        self.assertEqual(cookies, ["", ""])

    async def test_http_limiter_is_shared_by_all_requests(self) -> None:
        active = 0
        peak = 0

        async def handle(request: httpx.Request) -> httpx.Response:
            nonlocal active, peak
            del request
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return httpx.Response(200, text="ok")

        async with StatelessAsyncClient(
            transport=httpx.MockTransport(handle),
        ) as client:
            http = HttpTextClient(client, concurrency=2)
            await asyncio.gather(
                *(http.get(f"https://example.test/{index}") for index in range(8))
            )

        self.assertEqual(peak, 2)

    async def test_stateless_client_does_not_send_redirect_cookies(self) -> None:
        cookies: list[str] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            cookies.append(request.headers.get("cookie", ""))
            if request.url.path == "/first":
                return httpx.Response(
                    302,
                    headers={
                        "location": "/second",
                        "set-cookie": "session=secret; Path=/",
                    },
                )
            return httpx.Response(200)

        async with StatelessAsyncClient(
            transport=httpx.MockTransport(handle),
            follow_redirects=True,
        ) as client:
            await client.get("https://example.test/first")

        self.assertEqual(cookies, ["", ""])
