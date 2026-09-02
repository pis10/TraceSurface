from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from tracesurface.models import ScanJob, ScanSummary
from tracesurface.pipeline.messages import InferredItem, StageFailure
from tracesurface.sources import remove_scan_sources
from tracesurface.storage.commands import (
    CreateScan,
    FinishScan,
    InferenceWriteResult,
    PurgeTarget,
    SaveInference,
)
from tracesurface.storage.sqlite.writer import StorageWriter


@dataclass(slots=True)
class ScanLifecycle:
    storage_writer: StorageWriter
    target_replay_key_counts_loader: Callable[[str], dict[str, int]]
    wait_ms: int
    do_replay: bool
    replayed_key_counts: dict[str, int] = field(default_factory=dict)
    startup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cleaned_source_scans: set[int] = field(default_factory=set)
    prepared_scans: set[int] = field(default_factory=set)
    replayed_keys: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.replayed_keys = set(self.replayed_key_counts)

    async def prepare_target(self, target_url: str) -> ScanJob:
        async with self.startup_lock:
            removed_replay_keys: dict[str, int] = {}
            if self.do_replay:
                removed_replay_keys = await asyncio.to_thread(
                    self.target_replay_key_counts_loader,
                    target_url,
                )

            await self.storage_writer.submit(PurgeTarget(target_url))

            _subtract_key_counts(
                self.replayed_keys,
                self.replayed_key_counts,
                removed_replay_keys,
            )

            scan_id = await self.storage_writer.submit(
                CreateScan(target_url, self.wait_ms)
            )
            self.prepared_scans.add(scan_id)
        return ScanJob(target_url=target_url, scan_id=scan_id, wait_ms=self.wait_ms)

    async def finish_done(self, scan_id: int, summary: ScanSummary) -> None:
        await self.storage_writer.submit(
            FinishScan(scan_id, summary=summary, status="done")
        )

    async def fail_scan(self, item: StageFailure) -> None:
        if item.scan_id is None:
            return
        try:
            await self.storage_writer.submit(FinishScan(item.scan_id, status="failed"))
        finally:
            await self.cleanup_sources(item.scan_id)

    async def save_inference(self, item: InferredItem) -> InferenceWriteResult:
        scan_id = item.job.scan_id

        written = await self.storage_writer.submit(
            SaveInference(scan_id, inference=item.inference)
        )

        await self.cleanup_sources(scan_id)
        return written

    async def cleanup_sources(self, scan_id: int | None) -> None:
        if scan_id is None or scan_id in self.cleaned_source_scans:
            return

        await asyncio.to_thread(remove_scan_sources, scan_id)
        self.cleaned_source_scans.add(scan_id)
        self.prepared_scans.discard(scan_id)

    async def cleanup_remaining_sources(self) -> None:
        for scan_id in tuple(self.prepared_scans):
            await self.cleanup_sources(scan_id)


def _subtract_key_counts(
    keys: set[str],
    counts: dict[str, int],
    removed: dict[str, int],
) -> None:
    for key, n in removed.items():
        remaining = counts.get(key, 0) - n
        if remaining > 0:
            counts[key] = remaining
        else:
            counts.pop(key, None)
            keys.discard(key)
