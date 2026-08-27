from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import Awaitable, Callable, Iterable

from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.models import (
    ReplayJob,
    ReplayPlan,
    ReplayRecord,
    ReplayRequest,
    ReplayResult,
    ReplayStats,
)
from tracesurface.replay.dedup import ReplayDedupStore
from tracesurface.replay.plan import ReplayPlanBuilder
from tracesurface.replay.transport import HTTPTransport

ReplayRecordSink = Callable[[ReplayRecord], Awaitable[None] | None]
ReplayProgressSink = Callable[[int, int, ReplayStats], None]


def _replay_stats() -> ReplayStats:
    return {"total": 0, "s2xx": 0, "s3xx": 0, "s4xx": 0, "s5xx": 0, "serr": 0}


def _add_replay_stat(stats: ReplayStats, record: ReplayRecord) -> None:
    stats["total"] += 1

    if record.status is None:
        stats["serr"] += 1
    elif record.status < 300:
        stats["s2xx"] += 1
    elif record.status < 400:
        stats["s3xx"] += 1
    elif record.status < 500:
        stats["s4xx"] += 1
    else:
        stats["s5xx"] += 1


def _copy_replay_stats(stats: ReplayStats) -> ReplayStats:
    return ReplayStats(stats)


class ReplayService:
    def __init__(
        self,
        *,
        transport: HTTPTransport,
        dedup: ReplayDedupStore,
        limiter: asyncio.Semaphore,
        db_seen_keys: Iterable[str] | None = None,
        concurrency: int = DEFAULT_SETTINGS.replay.concurrency,
    ) -> None:
        self.transport = transport
        self.dedup = dedup
        self.limiter = limiter
        self.db_seen_keys = None if db_seen_keys is None else frozenset(db_seen_keys)
        self.concurrency = max(1, concurrency)

    async def run(
        self,
        plan: ReplayPlan,
        *,
        sink: ReplayRecordSink | None = None,
        on_progress: ReplayProgressSink | None = None,
    ) -> ReplayResult:
        write_lock = asyncio.Lock()
        stats = _replay_stats()
        completed = 0
        total = len(plan.requests)
        pending = deque(plan.requests)

        async def one(request: ReplayRequest) -> None:
            nonlocal completed
            try:
                if not await self.dedup.claim(
                    request.method,
                    request.url,
                    db_seen_keys=self.db_seen_keys,
                ):
                    return

                async with self.limiter:
                    record = await self.transport.send(
                        request,
                        scan_id=plan.scan_id,
                        referer=plan.target_url,
                    )

                async with write_lock:
                    if sink is not None:
                        maybe_awaitable = sink(record)

                        if inspect.isawaitable(maybe_awaitable):
                            await maybe_awaitable
                    _add_replay_stat(stats, record)
            finally:
                async with write_lock:
                    completed += 1
                    if on_progress is not None:
                        on_progress(completed, total, _copy_replay_stats(stats))

        async def worker() -> None:
            while pending:
                await one(pending.popleft())

        worker_count = min(total, self.concurrency)
        if worker_count:
            await asyncio.gather(*(worker() for _ in range(worker_count)))
        return ReplayResult(
            scan_id=plan.scan_id,
            target_url=plan.target_url,
            stats=stats,
        )


async def run_replay_job(
    job: ReplayJob,
    *,
    transport: HTTPTransport,
    limiter: asyncio.Semaphore,
    concurrency: int = DEFAULT_SETTINGS.replay.concurrency,
    on_progress: ReplayProgressSink | None = None,
    on_record: ReplayRecordSink | None = None,
    dedup_store: ReplayDedupStore | None = None,
) -> ReplayResult:
    plan = ReplayPlanBuilder().build(job)

    dedup = dedup_store or ReplayDedupStore()

    service = ReplayService(
        transport=transport,
        dedup=dedup,
        limiter=limiter,
        db_seen_keys=job.db_seen_keys,
        concurrency=concurrency,
    )
    return await service.run(
        plan,
        sink=on_record,
        on_progress=on_progress,
    )
