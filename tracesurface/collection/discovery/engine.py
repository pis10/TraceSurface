from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urlparse

from tracesurface.collection.discovery.explorer import Explorer
from tracesurface.collection.session import DiscoverySession
from tracesurface.config import CollectionSettings

DiscoveryHook = Callable[[DiscoverySession, int], Awaitable[None]]
DiscoveryFinalizer = Callable[[DiscoverySession], Awaitable[None]]


class DiscoveryEngine:
    def __init__(
        self,
        explorers: Sequence[Explorer],
        settings: CollectionSettings,
        *,
        before_round: DiscoveryHook | None = None,
        after_run: DiscoveryFinalizer | None = None,
    ) -> None:
        self.explorers = tuple(explorers)
        self.settings = settings
        self.before_round = before_round
        self.after_run = after_run

    async def run(self, session: DiscoverySession) -> None:
        run_once_done: set[str] = set()

        for round_num in range(self.settings.discovery_max_rounds):
            if self.before_round is not None:
                await self.before_round(session, round_num)

            before = session.discovery_fingerprint()
            for explorer in self.explorers:
                if explorer.run_once and explorer.name in run_once_done:
                    continue
                await self._run_explorer(session, explorer, round_num)
                if explorer.run_once:
                    run_once_done.add(explorer.name)

            if session.discovery_fingerprint() == before:
                break

        if self.after_run is not None:
            await self.after_run(session)

    async def _run_explorer(
        self,
        session: DiscoverySession,
        explorer,
        round_num: int,
    ) -> None:
        try:
            await explorer.discover(session, round_num)
        except RecursionError as exc:
            session.record_event(
                "recursion_error",
                explorer=explorer.name,
                round=round_num,
                detail=repr(exc),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session.record_event(
                "explorer_exception",
                explorer=explorer.name,
                round=round_num,
                detail=repr(exc),
            )


async def download_js_files(
    session: DiscoverySession,
    js_urls: set[str],
    *,
    referer: str,
) -> None:
    if not js_urls:
        return

    headers = {"Referer": referer}

    async def download(url: str) -> None:
        try:
            status_code, text, content_type = await session.ports.http.get_text(
                url,
                timeout_s=session.settings.js_download_timeout_s,
                headers=headers,
            )
        except Exception:
            return

        if status_code != 200:
            return
        if "html" in content_type.lower():
            return
        await session.add_js_source(url, text)

    await session.ports.http.map(js_urls, download)


def default_explorers() -> tuple[Explorer, ...]:
    from tracesurface.collection.artifacts.discovery_service import ArtifactExplorer
    from tracesurface.collection.runtime.route_runtime import RouteRuntimeExplorer

    return (ArtifactExplorer(), RouteRuntimeExplorer())


async def run_discovery_loop(session: DiscoverySession) -> None:
    parsed = urlparse(session.target_url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    async def download_pending(current: DiscoverySession) -> None:
        pending = set(current.facts.js_facts) - set(current.js_sources)
        if pending:
            await download_js_files(current, pending, referer=referer)

    await DiscoveryEngine(
        default_explorers(),
        session.settings,
        before_round=lambda current, _round: download_pending(current),
        after_run=download_pending,
    ).run(session)
