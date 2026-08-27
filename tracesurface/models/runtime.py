from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from tracesurface.frozen import FrozenDict
from tracesurface.models.analysis import ApiResolution, ExtractionFacts
from tracesurface.urls import dedup_key


@dataclass(frozen=True, slots=True)
class StackFrame:
    url: str = ""
    func: str = ""
    line: int = 0
    col: int = 0


@dataclass(slots=True)
class CDPRequest:
    request_url: str = ""
    request_path: str = ""
    method: str = ""
    query_string: str = ""
    post_data: str = ""
    content_type: str = ""
    frames: list[StackFrame] = field(default_factory=list)
    request_headers: dict[str, str] = field(default_factory=dict)
    response_status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: str = ""
    response_size: int = 0

    @property
    def dedup_key(self) -> str:
        return dedup_key(self.method, self.request_url)


@dataclass(slots=True)
class CDPResult:
    target_url: str = ""
    js_urls: set[str] = field(default_factory=set)
    js_sources: dict[str, str] = field(default_factory=dict)
    requests: list[CDPRequest] = field(default_factory=list)
    html_content: str = ""
    json_response_bodies: dict[str, str] = field(default_factory=dict)
    dropped_no_stack_count: int = 0
    dropped_no_stack_samples: list[str] = field(default_factory=list)
    timed_out: bool = False
    timeout_reasons: list[str] = field(default_factory=list)
    collection_error: str = ""


@dataclass(slots=True)
class RouteFact:
    path: str
    source: str
    evidence_url: str = ""
    visited: bool = False
    attempts: int = 0
    new_js: int = 0
    new_cdp: int = 0
    error: str = ""

    @property
    def productive(self) -> bool:
        return self.new_js > 0 or self.new_cdp > 0


@dataclass(frozen=True, slots=True)
class SourceRef:
    url: str
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SecretMatch:
    rule_id: str
    rule_group: str
    sensitive: bool
    value: str
    source_js: str
    line: int
    col_start: int
    context_before: str = ""
    context_line: str = ""
    context_after: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", FrozenDict(self.metadata))


ScanStatus: TypeAlias = Literal["running", "done", "failed"]


@dataclass(frozen=True, slots=True)
class ScanJob:
    target_url: str
    scan_id: int
    wait_ms: int


@dataclass(frozen=True, slots=True)
class ScanWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ScanSummary:
    js_count: int = 0
    ast_total: int = 0
    confirmed_count: int = 0
    route_count: int = 0
    visited_route_count: int = 0
    productive_route_count: int = 0
    cdp_request_count: int = 0
    cdp_only_count: int = 0
    not_inferred_count: int = 0
    tier_l1: int = 0
    tier_l2: int = 0
    tier_l3: int = 0
    tier_l4: int = 0
    secret_count: int = 0
    warnings: Sequence[ScanWarning] = ()
    skipped: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "warnings", tuple(self.warnings))


@dataclass(frozen=True, slots=True)
class ScanResult:
    target_url: str = ""
    js_count: int = 0
    cdp_request_count: int = 0
    ast_total: int = 0
    route_count: int = 0
    visited_route_count: int = 0
    productive_route_count: int = 0
    discovery_stats: Mapping[str, int] = field(default_factory=dict)
    base_urls: frozenset[str] = frozenset()
    apis: Sequence[ApiResolution] = ()
    cdp_only: Sequence[CDPRequest] = ()
    all_cdp_requests: Sequence[CDPRequest] = ()
    secrets: Sequence[SecretMatch] = ()
    warnings: Sequence[ScanWarning] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "discovery_stats", FrozenDict(self.discovery_stats))
        object.__setattr__(self, "base_urls", frozenset(self.base_urls))
        object.__setattr__(self, "apis", tuple(self.apis))
        object.__setattr__(self, "cdp_only", tuple(self.cdp_only))
        object.__setattr__(self, "all_cdp_requests", tuple(self.all_cdp_requests))
        object.__setattr__(self, "secrets", tuple(self.secrets))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    @property
    def confirmed(self) -> tuple[ApiResolution, ...]:
        return tuple(api for api in self.apis if api.status == "confirmed")

    @property
    def inferred(self) -> tuple[ApiResolution, ...]:
        return tuple(api for api in self.apis if api.status == "inferred")

    @property
    def ast_full_url(self) -> tuple[ApiResolution, ...]:
        return tuple(api for api in self.apis if api.status == "ast_full")

    @property
    def not_inferred(self) -> tuple[ApiResolution, ...]:
        return tuple(api for api in self.apis if api.status == "not_inferred")

    @property
    def confirmed_count(self) -> int:
        return len(self.confirmed)


@dataclass(frozen=True, slots=True)
class CollectionBundle:
    target_url: str = ""
    scan_id: int | None = None
    html_pages: Mapping[str, SourceRef] = field(default_factory=dict)
    js_sources: Mapping[str, SourceRef] = field(default_factory=dict)
    cdp_requests: Sequence[CDPRequest] = ()
    discovery_stats: Mapping[str, int] = field(default_factory=dict)
    route_facts: Sequence[RouteFact] = ()
    warnings: Sequence[ScanWarning] = ()
    skipped: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "html_pages", FrozenDict(self.html_pages))
        object.__setattr__(self, "js_sources", FrozenDict(self.js_sources))
        object.__setattr__(self, "cdp_requests", tuple(self.cdp_requests))
        object.__setattr__(self, "discovery_stats", FrozenDict(self.discovery_stats))
        object.__setattr__(self, "route_facts", tuple(self.route_facts))
        object.__setattr__(self, "warnings", tuple(self.warnings))


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    secrets: Sequence[SecretMatch] = ()
    js_count: int = 0
    facts: ExtractionFacts = field(default_factory=ExtractionFacts)

    def __post_init__(self) -> None:
        object.__setattr__(self, "secrets", tuple(self.secrets))


@dataclass(frozen=True, slots=True)
class InferenceResult:
    result: ScanResult = field(default_factory=ScanResult)


__all__ = [
    "StackFrame",
    "CDPRequest",
    "CDPResult",
    "RouteFact",
    "SourceRef",
    "SecretMatch",
    "ScanStatus",
    "ScanJob",
    "ScanWarning",
    "ScanSummary",
    "ScanResult",
    "CollectionBundle",
    "ExtractionResult",
    "InferenceResult",
]
