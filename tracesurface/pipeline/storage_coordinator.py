from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from tracesurface.models import (
    CDPReplayTarget,
    ReplayCandidate,
    ReplayJob,
    ReplayStats,
    ScanJob,
    ScanResult,
    ScanSummary,
)
from tracesurface.storage.commands import InferenceWriteResult
from tracesurface.urls import dedup_key
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
            written = await self.lifecycle.save_inference(item)
            if self.do_replay:
                replay_job = ReplayJob(
                    scan_id=item.job.scan_id,
                    target_url=item.job.target_url,
                    candidates=_replay_candidates(result, written.resolution_ids),
                    cdp_requests=_cdp_replay_targets(result, written),
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
    return ScanSummary(
        js_count=result.js_count,
        ast_total=result.ast_total,
        runtime_count=result.grade_count("runtime"),
        full_url_count=result.grade_count("full-url"),
        route_count=result.route_count,
        visited_route_count=result.visited_route_count,
        productive_route_count=result.productive_route_count,
        cdp_request_count=result.cdp_request_count,
        tier_l1=result.grade_count("L1"),
        tier_l2=result.grade_count("L2"),
        tier_l3=result.grade_count("L3"),
        tier_l4=result.grade_count("L4"),
        no_url_count=result.grade_count("no-url"),
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
            method=api.fact.method,
            full_url=api.full_url,
            grade=api.grade,
            params=api.fact.params,
            base_source=api.base_source,
            binding_rule=api.binding_rule,
            why_not_higher_tier=api.why_not_higher_tier,
        )
        for idx, api in enumerate(result.apis)
        if idx in resolution_id_map
    )


def _cdp_replay_targets(
    result: ScanResult,
    written: InferenceWriteResult,
) -> tuple[CDPReplayTarget, ...]:
    key_to_resolution: dict[str, int] = {}
    for idx, api in enumerate(result.apis):
        if api.confirmed is None:
            continue
        rid = written.resolution_ids.get(idx)
        if rid is None:
            continue
        key_to_resolution[dedup_key(api.confirmed.method, api.confirmed.url)] = rid

    return tuple(
        CDPReplayTarget(
            cdp_request_id=cid,
            method=req.method or "GET",
            url=req.request_url,
            raw_body=req.post_data,
            content_type=req.content_type,
            resolution_id=key_to_resolution.get(req.dedup_key),
        )
        for req in result.all_cdp_requests
        if req.request_url
        and (cid := written.cdp_ids.get(req.dedup_key)) is not None
    )
