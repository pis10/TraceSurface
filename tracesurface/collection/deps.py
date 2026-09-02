from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx

from tracesurface.config import DEFAULT_SETTINGS


class HttpClientTimeoutError(Exception):
    pass


T = TypeVar("T")
R = TypeVar("R")


async def run_cpu(
    executor: ProcessPoolExecutor,
    func: Callable[..., Any],
    *args: Any,
) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, func, *args)


@dataclass(frozen=True, slots=True)
class DiscoveryDeps:
    http: HttpTextClient
    cpu: ProcessPoolExecutor
    page: Any | None = None


class HttpTextClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        concurrency: int = DEFAULT_SETTINGS.http.concurrency,
    ) -> None:
        self.client = client
        self.concurrency = max(1, concurrency)
        self._semaphore = asyncio.Semaphore(self.concurrency)

    async def get_text(
        self,
        url: str,
        *,
        timeout_s: float,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        try:
            async with self._semaphore:
                resp = await self.client.get(url, timeout=timeout_s, headers=headers)
        except httpx.TimeoutException as exc:
            raise HttpClientTimeoutError(str(exc)) from exc
        text = await self.text(resp)
        return resp.status_code, text, resp.headers.get("content-type", "")

    async def get(self, url: str, **kwargs) -> httpx.Response:
        try:
            async with self._semaphore:
                return await self.client.get(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise HttpClientTimeoutError(str(exc)) from exc

    async def text(self, response: httpx.Response) -> str:
        return await asyncio.to_thread(_response_text, response)

    def stream(self, method: str, url: str, **kwargs):
        return self._stream(method, url, **kwargs)

    async def map(
        self,
        items: Collection[T],
        func: Callable[[T], Awaitable[R]],
    ) -> list[R]:
        iterator = iter(items)
        results: list[R] = []

        async def worker() -> None:
            for item in iterator:
                results.append(await func(item))

        worker_count = min(len(items), self.concurrency)
        if worker_count:
            await asyncio.gather(*(worker() for _ in range(worker_count)))
        return results

    @asynccontextmanager
    async def _stream(self, method: str, url: str, **kwargs):
        try:
            async with self._semaphore:
                async with self.client.stream(method, url, **kwargs) as response:
                    yield response
        except httpx.TimeoutException as exc:
            raise HttpClientTimeoutError(str(exc)) from exc


def _response_text(response: httpx.Response) -> str:
    return response.text or ""
