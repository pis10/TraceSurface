from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tracesurface.models import (
    CollectionBundle,
    InferenceResult,
    ReplayJob,
    ReplayResult,
    ReplayStats,
    ScanJob,
    ScanSummary,
    ScanWarning,
)


@dataclass(frozen=True, slots=True)
class CollectedItem:
    job: ScanJob
    bundle: CollectionBundle
    started_at: float


@dataclass(frozen=True, slots=True)
class InferredItem:
    job: ScanJob
    inference: InferenceResult
    started_at: float


@dataclass(frozen=True, slots=True)
class SkippedItem:
    job: ScanJob
    warnings: tuple[ScanWarning, ...]
    started_at: float


@dataclass(frozen=True, slots=True)
class ReplayPendingItem:
    job: ScanJob
    summary: ScanSummary
    replay_job: ReplayJob
    started_at: float


@dataclass(frozen=True, slots=True)
class ReplayDoneItem:
    job: ScanJob
    summary: ScanSummary
    started_at: float
    replay_result: ReplayResult | None = None
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class StageFailure:
    url: str
    scan_id: int | None
    stage: str
    error: BaseException
    started_at: float


@dataclass(frozen=True, slots=True)
class BatchScanOutcome:
    url: str
    ok: bool
    stats: ReplayStats | None = None
    error: str | None = None
    summary: ScanSummary | None = None
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class NoMoreAnalysis: ...


@dataclass(frozen=True, slots=True)
class ScanProgress:
    index: int
    total: int
    job: ScanJob
    summary: ScanSummary
    elapsed: float


@dataclass(frozen=True, slots=True)
class ScanOutput:
    success: Callable[[ScanProgress, ReplayStats], None]
    skipped: Callable[[ScanProgress], None]
    failure: Callable[[int, int, StageFailure], None]
