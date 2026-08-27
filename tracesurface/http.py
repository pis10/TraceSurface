from __future__ import annotations

from typing import Any

import httpx


class _DiscardingCookies(httpx.Cookies):
    def extract_cookies(self, response: httpx.Response) -> None:
        del response


class StatelessAsyncClient(httpx.AsyncClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cookies = _DiscardingCookies()
