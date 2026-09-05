from __future__ import annotations

import asyncio
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Browser

from tracesurface.collection.deps import HttpTextClient, run_cpu
from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.models import CollectionBundle, InferenceResult
from tracesurface.pipeline.lifecycle import ScanLifecycle
from tracesurface.pipeline.messages import (
    CollectedItem,
    InferredItem,
    NoMoreAnalysis,
    ReplayDoneItem,
    SkippedItem,
    StageFailure,
)


@dataclass(frozen=True, slots=True)
class RunConfig:
    site_concurrency: int
    cpu_workers: int
    http_concurrency: int
    replay_concurrency: int

    @classmethod
    def of(
        cls,
        *,
        site_concurrency: int,
        cpu_workers: int,
        replay_concurrency: int,
    ) -> "RunConfig":
        return cls(
            site_concurrency=max(1, site_concurrency),
            cpu_workers=max(1, cpu_workers),
            http_concurrency=DEFAULT_SETTINGS.http.concurrency,
            replay_concurrency=max(1, replay_concurrency),
        )


@dataclass(frozen=True, slots=True)
class PipelineQueues:
    job: asyncio.Queue[str | None]
    collection: asyncio.Queue[CollectedItem | None]
    storage: asyncio.Queue[
        InferredItem | SkippedItem | StageFailure | ReplayDoneItem | NoMoreAnalysis
    ]

    @classmethod
    def create(cls, config: RunConfig) -> "PipelineQueues":
        return cls(
            job=asyncio.Queue(),
            collection=asyncio.Queue(maxsize=max(1, config.site_concurrency * 2)),
            storage=asyncio.Queue(maxsize=max(1, config.cpu_workers * 2 + 1)),
        )

    def seed_jobs(self, urls: tuple[str, ...], worker_count: int) -> None:
        for url in urls:
            self.job.put_nowait(url)
        for _ in range(worker_count):
            self.job.put_nowait(None)


@dataclass(slots=True)
class StageWorkers:
    queues: PipelineQueues
    lifecycle: ScanLifecycle
    browser: Browser
    http: HttpTextClient
    cpu: ProcessPoolExecutor
    auth_state: dict[str, Any] | None = None
    headed: bool = False
    block_redirects: bool = False

    async def run_collector(self) -> None:
        from tracesurface.collection.service import collect_site

        while True:
            target_url = await self.queues.job.get()
            try:
                if target_url is None:
                    return
                started_at = time.perf_counter()
                scan_id: int | None = None
                try:
                    job = await self.lifecycle.prepare_target(target_url)
                    scan_id = job.scan_id
                    bundle = await collect_site(
                        target_url=job.target_url,
                        browser=self.browser,
                        wait_ms=job.wait_ms,
                        http=self.http,
                        cpu=self.cpu,
                        scan_id=job.scan_id,
                        auth_state=self.auth_state,
                        headed=self.headed,
                        block_redirects=self.block_redirects,
                    )

                    if bundle.skipped:
                        await self.queues.storage.put(
                            SkippedItem(
                                job=job,
                                warnings=tuple(bundle.warnings),
                                started_at=started_at,
                            )
                        )
                        continue

                    await self.queues.collection.put(
                        CollectedItem(job=job, bundle=bundle, started_at=started_at)
                    )
                except Exception as exc:
                    await self.queues.storage.put(
                        StageFailure(
                            url=target_url,
                            scan_id=scan_id,
                            stage="collect",
                            error=exc,
                            started_at=started_at,
                        )
                    )
            finally:
                self.queues.job.task_done()

    async def run_analysis(self) -> None:
        while True:
            item = await self.queues.collection.get()
            try:
                if item is None:
                    return
                try:
                    inference = await run_cpu(self.cpu, _analyze, item.bundle)
                    await self.queues.storage.put(
                        InferredItem(
                            job=item.job,
                            inference=inference,
                            started_at=item.started_at,
                        )
                    )
                except Exception as exc:
                    await self.queues.storage.put(
                        StageFailure(
                            url=item.job.target_url,
                            scan_id=item.job.scan_id,
                            stage="analysis",
                            error=exc,
                            started_at=item.started_at,
                        )
                    )
            finally:
                self.queues.collection.task_done()


def _analyze(bundle: CollectionBundle) -> InferenceResult:
    from tracesurface.inference.service import infer

    return infer(bundle)
