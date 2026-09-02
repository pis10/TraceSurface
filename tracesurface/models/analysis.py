from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    location: str
    default: object = None


ApiGrade: TypeAlias = Literal["runtime", "full-url", "L1", "L2", "L3", "L4", "no-url"]


@dataclass(frozen=True, slots=True)
class SourceLocation:
    url: str
    line: int
    col_start: int
    col_end: int


@dataclass(frozen=True, slots=True)
class CallerInfo:
    module_id: str = ""
    caller_var: str = ""
    caller_prop: str = ""
    require_id: str = ""


@dataclass(frozen=True, slots=True)
class ConfirmedRequest:
    method: str
    url: str
    path: str


@dataclass(frozen=True, slots=True)
class ApiResolution:
    fact: RequestFact
    grade: ApiGrade
    full_url: str | None = None
    base_url: str | None = None
    confirmed: ConfirmedRequest | None = None
    base_source: str | None = None
    binding_rule: str | None = None
    why_not_higher_tier: str | None = None


@dataclass(frozen=True, slots=True)
class ClientRef:
    module_id: str
    scope_id: int
    decl_node_id: int
    symbol_name: str = ""

    def key(self) -> tuple[str, int, int]:
        return (self.module_id, self.scope_id, self.decl_node_id)


EdgeKind: TypeAlias = Literal[
    "assign", "import", "export", "require", "wrapper_return", "default_export"
]


@dataclass(frozen=True, slots=True)
class ClientAliasFact:
    left_ref: ClientRef
    right_ref: ClientRef
    edge_kind: EdgeKind


@dataclass(frozen=True, slots=True)
class Lit:
    value: str


@dataclass(frozen=True, slots=True)
class EnvChoice:
    prod: ResolvedValue
    alternates: tuple[ResolvedValue, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteChoice:
    options: tuple[ResolvedValue, ...]


@dataclass(frozen=True, slots=True)
class RefHole:
    client_ref: ClientRef | None = None
    display: str = ""


@dataclass(frozen=True, slots=True)
class DynamicHole:
    reason: str = ""


ResolvedValue: TypeAlias = Lit | EnvChoice | RouteChoice | RefHole | DynamicHole


@dataclass(frozen=True, slots=True)
class UrlTemplate:
    segments: tuple[ResolvedValue, ...]


@dataclass(frozen=True, slots=True)
class RequestFact:
    request_id: str
    method: str
    path: str
    url_template: UrlTemplate
    client_refs: tuple[ClientRef, ...]
    params: tuple[Param, ...]
    location: SourceLocation
    caller: CallerInfo = CallerInfo()
    pattern: str = ""


BaseSourceKind: TypeAlias = Literal[
    "cdp_derived", "static_config", "interceptor", "sdk_init", "inline_host", "origin"
]


@dataclass(frozen=True, slots=True)
class BaseFact:
    base_id: str
    base_value: ResolvedValue
    client_refs: tuple[ClientRef, ...]
    source_kind: BaseSourceKind
    location: SourceLocation
    client: str = ""
    require_id: str = ""
    module_id: str = ""
    local_var: str = ""
    js_url: str = ""


@dataclass(frozen=True, slots=True)
class ExtractionFacts:
    requests: tuple[RequestFact, ...] = ()
    bases: tuple[BaseFact, ...] = ()
    aliases: tuple[ClientAliasFact, ...] = ()


__all__ = [
    "Param",
    "ApiGrade",
    "SourceLocation",
    "CallerInfo",
    "ConfirmedRequest",
    "ApiResolution",
    "ClientRef",
    "EdgeKind",
    "ClientAliasFact",
    "Lit",
    "EnvChoice",
    "RouteChoice",
    "RefHole",
    "DynamicHole",
    "ResolvedValue",
    "UrlTemplate",
    "RequestFact",
    "BaseSourceKind",
    "BaseFact",
    "ExtractionFacts",
]
