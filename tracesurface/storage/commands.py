from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from tracesurface.models import InferenceResult, ReplayRecord, ScanStatus, ScanSummary


@dataclass(frozen=True, slots=True)
class CreateScan:
    target_url: str
    wait_ms: int


@dataclass(frozen=True, slots=True)
class PurgeTarget:
    target_url: str


@dataclass(frozen=True, slots=True)
class PurgeAll: ...


@dataclass(frozen=True, slots=True)
class InferenceWriteResult:
    resolution_ids: dict[int, int]
    cdp_ids: dict[str, int]


@dataclass(frozen=True, slots=True)
class SaveInference:
    scan_id: int
    inference: InferenceResult


@dataclass(frozen=True, slots=True)
class SaveReplayRecord:
    record: ReplayRecord


@dataclass(frozen=True, slots=True)
class FinishScan:
    scan_id: int
    summary: ScanSummary | None = None
    status: ScanStatus = "done"


@dataclass(frozen=True, slots=True)
class Flush: ...


StorageCommand: TypeAlias = (
    CreateScan
    | PurgeTarget
    | PurgeAll
    | SaveInference
    | SaveReplayRecord
    | FinishScan
    | Flush
)
