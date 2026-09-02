from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TypedDict

from tracesurface.frozen import FrozenDict
from tracesurface.models.analysis import Param


class ReplayStats(TypedDict):
    total: int
    s2xx: int
    s3xx: int
    s4xx: int
    s5xx: int
    serr: int


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    resolution_id: int | None
    method: str
    url: str
    query: Mapping[str, object] = field(default_factory=dict)
    body: Mapping[str, object] | None = None
    variant: str = ""
    inference_tier: str | None = None
    base_source: str | None = None
    binding_rule: str | None = None
    why_not_higher_tier: str | None = None
    origin: str = "inferred"
    cdp_request_id: int | None = None
    raw_body: str | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", FrozenDict(self.query))
        if self.body is not None:
            object.__setattr__(self, "body", FrozenDict(self.body))


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    scan_id: int
    target_url: str
    requests: tuple[ReplayRequest, ...] = ()


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    resolution_id: int | None
    scan_id: int
    domain: str
    variant: str
    sent_url: str
    sent_method: str
    sent_query: str | None
    sent_body: str | None
    sent_headers: str | None
    status: int | None
    resp_headers: str | None
    resp_ct: str
    resp_len: int
    resp_body: str | None
    time_ms: int
    error: str | None
    resp_bytes: bytes | None = None
    inference_tier: str | None = None
    base_source: str | None = None
    binding_rule: str | None = None
    why_not_higher_tier: str | None = None
    cdp_request_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReplayCandidate:
    resolution_id: int
    method: str
    full_url: str | None
    status: str
    params: tuple[Param, ...] = ()
    tier: str | None = None
    base_source: str | None = None
    binding_rule: str | None = None
    why_not_higher_tier: str | None = None


@dataclass(frozen=True, slots=True)
class CDPReplayTarget:
    cdp_request_id: int
    method: str
    url: str
    raw_body: str | None = None
    content_type: str | None = None
    resolution_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReplayJob:
    scan_id: int
    target_url: str
    candidates: tuple[ReplayCandidate, ...] = ()
    cdp_requests: tuple[CDPReplayTarget, ...] = ()
    db_seen_keys: Iterable[str] = field(default_factory=frozenset)
    allow_destructive: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "cdp_requests", tuple(self.cdp_requests))
        object.__setattr__(self, "db_seen_keys", frozenset(self.db_seen_keys))


@dataclass(frozen=True, slots=True)
class ReplayResult:
    scan_id: int
    target_url: str
    stats: ReplayStats | Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stats", FrozenDict(self.stats))


__all__ = [
    "ReplayStats",
    "ReplayRequest",
    "ReplayPlan",
    "ReplayRecord",
    "ReplayCandidate",
    "CDPReplayTarget",
    "ReplayJob",
    "ReplayResult",
]
