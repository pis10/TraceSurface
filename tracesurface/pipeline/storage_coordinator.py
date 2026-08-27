from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from tracesurface.models import (
    ReplayCandidate,
    ReplayJob,
    ReplayStats,
    ScanJob,
    ScanResult,
    ScanSummary,
)
from tracesurface.pipeline.lifecycle import ScanLifecycle
from tracesurface.pipeline.messages import (
    InferredItem,
    NoMoreAnalysis,
    ReplayDoneItem,
    ReplayPendingItem,
    SkippedItem,
    StageFailure,
)
from tracesurface.pipeline.outcome import OutcomeRecorder
from tracesurface.pipeline.replay_scheduler import ReplayScheduler

StorageItem = (
    InferredItem | SkippedItem | StageFailure | ReplayDoneItem | NoMoreAnalysis
)


@dataclass(slots=True)
class StorageCoordinator:
    lifecycle: ScanLifecycle
    replay_scheduler: ReplayScheduler
    recorder: OutcomeRecorder
    do_replay: bool
    allow_destructive: bool
    pending_replays: int = 0
    no_more_analysis: bool = False

    async def run(self, queue: asyncio.Queue[StorageItem]) -> None:
        while True:
            item = await queue.get()
            try:
                await self._handle(item)

                if self._done():
                    return
            finally:
                queue.task_done()

    async def _handle(self, item: StorageItem) -> None:
        if isinstance(item, NoMoreAnalysis):
            self.no_more_analysis = True
            return

        if isinstance(item, StageFailure):
            await self.lifecycle.fail_scan(item)
            self.recorder.record_failure(item)
            return
        if isinstance(item, ReplayDoneItem):
            await self._handle_replay_done(item)
            return
        if isinstance(item, SkippedItem):
            await self._handle_skipped(item)
            return

        await self._handle_inferred(item)

    async def _handle_replay_done(self, item: ReplayDoneItem) -> None:
        try:
            if item.error is not None:
                await self._fail_storage(item.job, item.started_at, item.error)
                return

            assert item.replay_result is not None

            await self.lifecycle.finish_done(item.job.scan_id, item.summary)
            self.recorder.record_success(
                item.job,
                item.summary,
                cast(ReplayStats, item.replay_result.stats),
                item.started_at,
            )
        except Exception as exc:
            await self._fail_storage(item.job, item.started_at, exc)
        finally:
            self.pending_replays -= 1

    async def _handle_inferred(self, item: InferredItem) -> None:
        result = item.inference.result
        summary = _summary_from_result(result)
        try:
            resolution_id_map = await self.lifecycle.save_inference(item)
            if self.do_replay:
                cdp_targets = await self.lifecycle.load_cdp_replay_targets(
                    item.job.scan_id
                )
                replay_job = ReplayJob(
                    scan_id=item.job.scan_id,
                    target_url=item.job.target_url,
                    candidates=_replay_candidates(result, resolution_id_map),
                    cdp_requests=cdp_targets,
                    db_seen_keys=self.lifecycle.replayed_keys,
                    allow_destructive=self.allow_destructive,
                )

                self.pending_replays += 1
                self.replay_scheduler.submit(
                    ReplayPendingItem(
                        job=item.job,
                        summary=summary,
                        replay_job=replay_job,
                        started_at=item.started_at,
                    )
                )

                return

            await self.lifecycle.finish_done(item.job.scan_id, summary)
            self.recorder.record_success(
                item.job,
                summary,
                _empty_replay_stats(),
                item.started_at,
            )
        except Exception as exc:
            await self._fail_storage(item.job, item.started_at, exc)

    async def _handle_skipped(self, item: SkippedItem) -> None:
        summary = ScanSummary(warnings=item.warnings, skipped=True)
        try:
            await self.lifecycle.finish_done(item.job.scan_id, summary)
            await self.lifecycle.cleanup_sources(item.job.scan_id)
            self.recorder.record_skipped(item.job, summary, item.started_at)
        except Exception as exc:
            await self._fail_storage(item.job, item.started_at, exc)

    async def _fail_storage(
        self,
        job: ScanJob,
        started_at: float,
        exc: BaseException,
    ) -> None:
        failed = StageFailure(
            url=job.target_url,
            scan_id=job.scan_id,
            stage="storage",
            error=exc,
            started_at=started_at,
        )
        await self.lifecycle.fail_scan(failed)
        self.recorder.record_failure(failed)

    def _done(self) -> bool:
        return (
            self.no_more_analysis
            and self.pending_replays == 0
            and self.recorder.done_count >= self.recorder.total
        )


def _empty_replay_stats() -> ReplayStats:
    return {"total": 0, "s2xx": 0, "s3xx": 0, "s4xx": 0, "s5xx": 0, "serr": 0}


def _summary_from_result(result: ScanResult) -> ScanSummary:
    inferred = result.inferred

    tier_l1 = len(result.ast_full_url) + sum(1 for m in inferred if m.tier == "L1")
    tier_l2 = sum(1 for m in inferred if m.tier == "L2")
    tier_l3 = sum(1 for m in inferred if m.tier == "L3")
    tier_l4 = sum(1 for m in inferred if m.tier == "L4")
    return ScanSummary(
        js_count=result.js_count,
        ast_total=result.ast_total,
        confirmed_count=result.confirmed_count,
        route_count=result.route_count,
        visited_route_count=result.visited_route_count,
        productive_route_count=result.productive_route_count,
        cdp_request_count=result.cdp_request_count,
        cdp_only_count=len(result.cdp_only),
        not_inferred_count=len(result.not_inferred),
        tier_l1=tier_l1,
        tier_l2=tier_l2,
        tier_l3=tier_l3,
        tier_l4=tier_l4,
        secret_count=len(result.secrets),
        warnings=tuple(result.warnings),
    )


def _replay_candidates(
    result: ScanResult,
    resolution_id_map: dict[int, int],
) -> tuple[ReplayCandidate, ...]:
    return tuple(
        ReplayCandidate(
            resolution_id=resolution_id_map[idx],
            method=api.candidate.method,
            full_url=api.full_url,
            status=api.status,
            params=api.candidate.params,
            tier=api.tier,
            base_source=api.base_source,
            binding_rule=api.binding_rule,
            why_not_higher_tier=api.why_not_higher_tier,
        )
        for idx, api in enumerate(result.apis)
        if idx in resolution_id_map
    )
