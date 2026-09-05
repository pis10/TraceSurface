from __future__ import annotations

import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from playwright.async_api import async_playwright

from tracesurface.collection.deps import HttpTextClient
from tracesurface.collection.runtime.browser_context import launch_browser
from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.http import StatelessAsyncClient
from tracesurface.pipeline.lifecycle import ScanLifecycle
from tracesurface.pipeline.messages import BatchScanOutcome, NoMoreAnalysis, ScanOutput
from tracesurface.pipeline.outcome import OutcomeRecorder
from tracesurface.pipeline.replay_scheduler import ReplayScheduler
from tracesurface.pipeline.storage_coordinator import StorageCoordinator
from tracesurface.pipeline.workers import PipelineQueues, RunConfig, StageWorkers
from tracesurface.replay.service import run_replay_job
from tracesurface.replay.transport import HTTPTransport
from tracesurface.storage.sqlite.writer import open_writer
from tracesurface.ui import configure_worker_logging


@dataclass(frozen=True, slots=True)
class ScanRequest:
    urls: tuple[str, ...]
    wait_ms: int
    site_concurrency: int
    replay_concurrency: int
    do_replay: bool
    output: ScanOutput
    cpu_workers: int = DEFAULT_SETTINGS.workers.cpu_workers
    auth_state: dict[str, Any] | None = None
    headed: bool = False
    allow_destructive: bool = False
    block_redirects: bool = False


class PipelineRunner:
    async def run(self, request: ScanRequest) -> list[BatchScanOutcome]:
        config = RunConfig.of(
            site_concurrency=request.site_concurrency,
            cpu_workers=request.cpu_workers,
            replay_concurrency=request.replay_concurrency,
        )
        replayed_key_counts = (
            await asyncio.to_thread(_load_replayed_keys) if request.do_replay else {}
        )
        results: list[BatchScanOutcome] = []
        recorder = OutcomeRecorder(
            total=len(request.urls),
            output=request.output,
            results=results,
        )
        queues = PipelineQueues.create(config)
        queues.seed_jobs(request.urls, config.site_concurrency)

        cpu_executor = ProcessPoolExecutor(
            max_workers=config.cpu_workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=configure_worker_logging,
        )
        storage_writer = None
        lifecycle = None

        try:
            storage_writer = await asyncio.to_thread(open_writer)
            await storage_writer.start()
            async with AsyncExitStack() as resources:
                collection_client = await resources.enter_async_context(
                    StatelessAsyncClient(
                        follow_redirects=True,
                        headers={
                            "User-Agent": DEFAULT_SETTINGS.browser.user_agent,
                        },
                        verify=DEFAULT_SETTINGS.http.tls_verify,
                        limits=_http_limits(config.http_concurrency),
                    )
                )
                http = HttpTextClient(
                    collection_client,
                    concurrency=config.http_concurrency,
                )
                replay_client = await resources.enter_async_context(
                    HTTPTransport.create_client(
                        max_connections=config.replay_concurrency,
                    )
                )
                replay_limiter = asyncio.Semaphore(config.replay_concurrency)

                playwright = await async_playwright().start()
                resources.push_async_callback(playwright.stop)
                browser = await launch_browser(playwright, headless=not request.headed)
                resources.push_async_callback(browser.close)

                lifecycle = ScanLifecycle(
                    storage_writer=storage_writer,
                    target_replay_key_counts_loader=_load_target_replayed_keys,
                    wait_ms=request.wait_ms,
                    do_replay=request.do_replay,
                    replayed_key_counts=replayed_key_counts,
                )
                replay_scheduler = ReplayScheduler(
                    storage_writer=storage_writer,
                    replay_concurrency=config.replay_concurrency,
                    output_queue=queues.storage,
                    run_replay_job=partial(
                        run_replay_job,
                        transport=HTTPTransport(replay_client),
                        limiter=replay_limiter,
                    ),
                )
                coordinator = StorageCoordinator(
                    lifecycle=lifecycle,
                    replay_scheduler=replay_scheduler,
                    recorder=recorder,
                    do_replay=request.do_replay,
                    allow_destructive=request.allow_destructive,
                )
                workers = StageWorkers(
                    queues=queues,
                    lifecycle=lifecycle,
                    browser=browser,
                    http=http,
                    cpu=cpu_executor,
                    auth_state=request.auth_state,
                    headed=request.headed,
                    block_redirects=request.block_redirects,
                )

                tasks: list[asyncio.Task[None]] = []
                try:
                    collectors = [
                        asyncio.create_task(workers.run_collector())
                        for _ in range(config.site_concurrency)
                    ]
                    analyses = [
                        asyncio.create_task(workers.run_analysis())
                        for _ in range(config.cpu_workers)
                    ]
                    storage_task = asyncio.create_task(
                        coordinator.run(queues.storage)
                    )
                    tasks = [*collectors, *analyses, storage_task]

                    await asyncio.gather(*collectors)
                    for _ in analyses:
                        await queues.collection.put(None)
                    await asyncio.gather(*analyses)

                    await queues.storage.put(NoMoreAnalysis())
                    await storage_task
                    await replay_scheduler.join()
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    await replay_scheduler.shutdown()
        finally:
            try:
                if storage_writer is not None:
                    await storage_writer.stop()
            finally:
                try:
                    await asyncio.to_thread(
                        cpu_executor.shutdown,
                        wait=True,
                        cancel_futures=True,
                    )
                finally:
                    if lifecycle is not None:
                        await lifecycle.cleanup_remaining_sources()

        return results


def _http_limits(concurrency: int) -> httpx.Limits:
    concurrency = max(1, concurrency)
    return httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )


def _load_replayed_keys() -> dict[str, int]:
    from tracesurface.storage.sqlite.connection import init
    from tracesurface.storage.sqlite.repositories import load_replayed_key_counts

    init()
    return load_replayed_key_counts()


def _load_target_replayed_keys(target_url: str) -> dict[str, int]:
    from tracesurface.storage.sqlite.connection import init
    from tracesurface.storage.sqlite.repositories import (
        load_replayed_key_counts_for_target,
    )

    init()
    return load_replayed_key_counts_for_target(target_url)



