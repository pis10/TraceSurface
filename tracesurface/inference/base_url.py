from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TypeAlias, TypeVar
from urllib.parse import urlparse

from tracesurface.models import ApiResolution, BaseFact, EnvChoice, Lit, ResolvedValue
from tracesurface.urls import combine_urls, dedup_key, is_absolute_url

EXPR = "EXPR"
ScopeKey: TypeAlias = tuple[str, str]
MapKey = TypeVar("MapKey")

_GRADE_RANK = {
    "runtime": 6,
    "full-url": 5,
    "L1": 4,
    "L2": 3,
    "L3": 2,
    "L4": 1,
    "no-url": 0,
}


@dataclass(frozen=True, slots=True)
class BaseUrlAnchors:
    require_base: dict[str, str]
    scope_base: dict[ScopeKey, str]
    handle_base: dict[str, str]
    jsurl_bases: dict[str, set[str]]
    all_base_urls: set[str]
    origin: str = ""
    cdp_bases: frozenset[str] = frozenset()
    static_bases: frozenset[str] = frozenset()
    require_fanout: dict[str, set[str]] = field(default_factory=dict)
    scope_fanout: dict[ScopeKey, set[str]] = field(default_factory=dict)

    def source_of(self, base: str | None) -> str:
        if not base:
            return ""
        if base in self.cdp_bases:
            return "cdp"
        if base in self.static_bases:
            return "static"
        return ""


def compute_base_url(runtime_url: str, ast_path: str) -> str | None:
    if not ast_path:
        return None

    dynamic_prefix = ast_path.startswith(EXPR)
    if dynamic_prefix:
        ast_path = _path_without_query_or_fragment(_strip_expr_prefix(ast_path))
    elif not ast_path.startswith("/"):
        return None
    else:
        ast_path = _path_without_query_or_fragment(ast_path)

    if EXPR in ast_path:
        return None

    ast_segments = [s for s in ast_path.split("/") if s]
    if not ast_segments:
        return None
    parsed = urlparse(runtime_url)
    url_path_segments = [s for s in parsed.path.split("/") if s]

    if len(ast_segments) > len(url_path_segments):
        return None

    if dynamic_prefix and not _tail_matches_ast_path(ast_segments, url_path_segments):
        return None

    remaining = url_path_segments[: len(url_path_segments) - len(ast_segments)]
    base_path = "/" + "/".join(remaining) if remaining else ""
    base = f"{parsed.scheme}://{parsed.netloc}{base_path}"

    if not base.startswith("http"):
        return None
    return base.rstrip("/")


def build_base_url_anchors(
    confirmed: list[ApiResolution],
    base_facts: tuple[BaseFact, ...] = (),
    origin: str = "",
) -> BaseUrlAnchors:
    require_base, require_conf = _build_first_map(
        confirmed,
        key_fn=lambda r: r.fact.caller.require_id,
        value_fn=_base_from_confirmed,
    )
    scope_base, scope_conf = _build_first_map(
        confirmed,
        key_fn=lambda r: (
            (
                r.fact.caller.module_id,
                r.fact.caller.caller_var,
            )
            if r.fact.caller.module_id and r.fact.caller.caller_var
            else None
        ),
        value_fn=_base_from_confirmed,
    )
    handle_base, handle_conf = _build_first_map(
        confirmed,
        key_fn=lambda r: (
            r.fact.caller.caller_var
            if r.fact.caller.caller_var
            and _is_distinctive_handle(r.fact.caller.caller_var)
            else None
        ),
        value_fn=_base_from_confirmed,
    )
    jsurl_bases = _build_all_map(
        confirmed,
        key_fn=lambda r: r.fact.location.url,
        value_fn=_base_from_confirmed,
    )

    for key in require_conf:
        require_base.pop(key, None)
    for key in scope_conf:
        scope_base.pop(key, None)
    for key in handle_conf:
        handle_base.pop(key, None)

    cdp_bases: set[str] = set()
    cdp_bases.update(require_base.values())
    cdp_bases.update(scope_base.values())
    cdp_bases.update(handle_base.values())
    for values in jsurl_bases.values():
        cdp_bases.update(values)

    static_bases = {
        _absolutize(base, origin)
        for fact in base_facts
        if (base := _base_value(fact.base_value))
    }

    static_require: dict[str, set[str]] = {}
    static_scope: dict[ScopeKey, set[str]] = {}
    for fact in base_facts:
        base = _base_value(fact.base_value)
        if not base:
            continue
        base_abs = _absolutize(base, origin)
        if not base_abs:
            continue
        if fact.require_id:
            static_require.setdefault(fact.require_id, set()).add(base_abs)
        if fact.module_id and fact.local_var:
            static_scope.setdefault((fact.module_id, fact.local_var), set()).add(
                base_abs
            )

        if fact.js_url and not fact.require_id and not fact.local_var:
            jsurl_bases.setdefault(fact.js_url, set()).add(base_abs)

    require_fanout: dict[str, set[str]] = {}
    for rid, bases in static_require.items():
        if rid in require_base:
            continue
        if len(bases) == 1:
            require_base[rid] = next(iter(bases))
        else:
            require_fanout[rid] = bases

    scope_fanout: dict[ScopeKey, set[str]] = {}
    for skey, bases in static_scope.items():
        if skey in scope_base:
            continue
        if len(bases) == 1:
            scope_base[skey] = next(iter(bases))
        else:
            scope_fanout[skey] = bases

    all_base_urls: set[str] = set()
    all_base_urls.update(require_base.values())
    all_base_urls.update(scope_base.values())
    all_base_urls.update(handle_base.values())
    for values in jsurl_bases.values():
        all_base_urls.update(values)
    for values in require_fanout.values():
        all_base_urls.update(values)
    for values in scope_fanout.values():
        all_base_urls.update(values)

    return BaseUrlAnchors(
        require_base=require_base,
        scope_base=scope_base,
        handle_base=handle_base,
        jsurl_bases=jsurl_bases,
        all_base_urls=all_base_urls,
        origin=origin,
        cdp_bases=frozenset(cdp_bases),
        static_bases=frozenset(static_bases),
        require_fanout=require_fanout,
        scope_fanout=scope_fanout,
    )


def propagate_methods(
    resolutions: tuple[ApiResolution, ...],
    confirmed: list[ApiResolution],
) -> tuple[ApiResolution, ...]:
    prop_method, _ = _build_first_map(
        confirmed,
        key_fn=lambda r: (
            (
                r.fact.caller.require_id,
                r.fact.caller.caller_prop,
            )
            if r.fact.caller.require_id and r.fact.caller.caller_prop
            else None
        ),
        value_fn=lambda r: r.confirmed.method if r.confirmed else None,
    )

    if not prop_method:
        return resolutions

    propagated: list[ApiResolution] = []
    for resolution in resolutions:
        if resolution.fact.method != "UNKNOWN":
            propagated.append(resolution)
            continue

        caller = resolution.fact.caller
        key = (caller.require_id, caller.caller_prop)
        method = prop_method.get(key) if key[0] and key[1] else None
        propagated.append(_with_method(resolution, method) if method else resolution)
    return tuple(propagated)


def dedup_in_scan(resolutions: tuple[ApiResolution, ...]) -> tuple[ApiResolution, ...]:
    return tuple(_dedup_in_scan(list(resolutions)))


def _strip_expr_prefix(ast_path: str) -> str:
    while ast_path.startswith(EXPR):
        ast_path = ast_path[len(EXPR) :]

    if ast_path and not ast_path.startswith("/"):
        ast_path = "/" + ast_path
    return ast_path


def _path_without_query_or_fragment(path: str) -> str:
    return path.split("?", 1)[0].split("#", 1)[0]


def _dynamic_segment_matches(ast_segment: str, runtime_segment: str) -> bool:
    if EXPR not in ast_segment:
        return ast_segment == runtime_segment

    pattern = re.escape(ast_segment).replace(re.escape(EXPR), ".*")
    return re.fullmatch(pattern, runtime_segment) is not None


def _tail_matches_ast_path(
    ast_segments: list[str],
    runtime_segments: list[str],
) -> bool:
    tail = runtime_segments[-len(ast_segments) :]
    return all(
        _dynamic_segment_matches(ast_segment, runtime_segment)
        for ast_segment, runtime_segment in zip(ast_segments, tail)
    )


def _build_first_map(
    confirmed_apis: list[ApiResolution],
    key_fn: Callable[[ApiResolution], MapKey | None],
    value_fn: Callable[[ApiResolution], str | None],
) -> tuple[dict[MapKey, str], dict[MapKey, set[str]]]:
    result: dict[MapKey, str] = {}
    conflicts: dict[MapKey, set[str]] = {}
    for resolution in confirmed_apis:
        value = value_fn(resolution)

        if not value:
            continue
        key = key_fn(resolution)
        if not key:
            continue
        if key not in result:
            result[key] = value
        elif result[key] != value:
            if key not in conflicts:
                conflicts[key] = {result[key]}
            conflicts[key].add(value)
    return result, conflicts


def _build_all_map(
    confirmed_apis: list[ApiResolution],
    key_fn: Callable[[ApiResolution], MapKey | None],
    value_fn: Callable[[ApiResolution], str | None],
) -> dict[MapKey, set[str]]:
    result: dict[MapKey, set[str]] = {}
    for resolution in confirmed_apis:
        value = value_fn(resolution)
        if not value:
            continue
        key = key_fn(resolution)
        if not key:
            continue
        result.setdefault(key, set()).add(value)
    return result


def _base_from_confirmed(resolution: ApiResolution) -> str | None:
    if not resolution.confirmed:
        return None
    return compute_base_url(resolution.confirmed.url, resolution.fact.path)


_GENERIC_VERBS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "send",
    "call",
    "run",
    "exec",
    "invoke",
}
_HANDLE_SUBSTRINGS = ("api", "http", "fetch", "request", "client")
_FETCH_SUBSTR_EXCLUDE_PREFIXES = ("prefetch", "refetch")


def _is_distinctive_handle(name: str) -> bool:
    if not name or len(name) < 3:
        return False
    low = name.lower()

    if low in _GENERIC_VERBS:
        return False

    if any(low.startswith(p) for p in _FETCH_SUBSTR_EXCLUDE_PREFIXES):
        return False

    if name.startswith("$"):
        return True

    return any(s in low for s in _HANDLE_SUBSTRINGS)


def _build_full_url(base_url: str, ast_path: str) -> str:
    path = _strip_expr_prefix(ast_path)
    return combine_urls(base_url, path)


def _with_method(resolution: ApiResolution, method: str) -> ApiResolution:
    fact = replace(resolution.fact, method=method)
    return replace(resolution, fact=fact)


def _dedup_in_scan(resolutions: list[ApiResolution]) -> list[ApiResolution]:
    best: dict[str, ApiResolution] = {}
    for resolution in resolutions:
        if resolution.grade == "runtime" or not resolution.full_url:
            continue
        key = dedup_key(resolution.fact.method or "UNKNOWN", resolution.full_url)
        cur = best.get(key)

        if cur is None or _GRADE_RANK.get(resolution.grade, 0) > _GRADE_RANK.get(
            cur.grade, 0
        ):
            best[key] = resolution

    seen: set[str] = set()
    out: list[ApiResolution] = []
    for resolution in resolutions:
        if resolution.grade == "runtime" or not resolution.full_url:
            out.append(resolution)
            continue
        key = dedup_key(resolution.fact.method or "UNKNOWN", resolution.full_url)
        if key in seen:
            continue
        seen.add(key)
        out.append(best[key])
    return out


def _absolutize(base: str, origin: str) -> str:
    if is_absolute_url(base):
        return base.rstrip("/")

    if base.startswith("/") and origin:
        return (origin + base).rstrip("/")

    return base.rstrip("/")


def _base_value(value: ResolvedValue) -> str | None:
    if isinstance(value, Lit):
        return value.value or None
    if isinstance(value, EnvChoice):
        return _base_value(value.prod)
    return None
