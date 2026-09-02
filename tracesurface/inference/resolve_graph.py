from __future__ import annotations

from dataclasses import replace

from tracesurface.inference.base_url import (
    BaseUrlAnchors,
    _absolutize,
    _build_full_url,
    _is_distinctive_handle,
    compute_base_url,
)
from tracesurface.inference.client_graph import ClientGraph, ClientKey
from tracesurface.models import (
    ApiGrade,
    ApiResolution,
    BaseFact,
    EnvChoice,
    Lit,
    RefHole,
    ResolvedValue,
)

WHY_GRAPH_BASE = "无唯一 anchor 绑定（require_id/module_scope/distinctive handle 均未命中），改由 client 身份图绑 base"
WHY_REQUIRE_FANOUT = "require 实例对应多个 base，确定性扇出（无法挑唯一）"
WHY_SCOPE_FANOUT = "scope 实例对应多个 base，确定性扇出（无法挑唯一）"
WHY_FILE_FANOUT = "无 client/require/scope 绑定，按文件级 base 扇出"
WHY_GLOBAL_POOL = "无 client/require/scope/file 绑定，回退全站「已发现」base 池扇出"
WHY_ORIGIN_FALLBACK = "无任何已发现的 base，仅余 target origin 兜底（零代码证据）"


def resolve_graph(
    resolutions: tuple[ApiResolution, ...],
    anchors: BaseUrlAnchors,
    client_graph: ClientGraph,
    base_facts: tuple[BaseFact, ...],
    origin: str,
) -> tuple[ApiResolution, ...]:
    normalized = [_normalize_initial(r) for r in resolutions]

    base_by_canon = _bases_by_canonical(normalized, client_graph, base_facts, origin)
    graph_bases = {b for b, _src in base_by_canon.values()}

    pool = anchors.all_base_urls | graph_bases
    if anchors.origin:
        pool = pool | {anchors.origin}
    if not pool:
        return tuple(normalized)

    out: list[ApiResolution] = []
    for resolution in normalized:
        if resolution.full_url or resolution.grade == "runtime":
            out.append(resolution)
            continue

        caller = resolution.fact.caller

        base, rule = _unique_anchor(caller, anchors)
        if base:
            out.append(
                _bound(
                    resolution,
                    base,
                    "L1",
                    anchors.source_of(base) or "static",
                    rule,
                )
            )
            continue

        graph_base = _graph_base(resolution, client_graph, base_by_canon)
        if graph_base is not None:
            base, source, rule = graph_base
            out.append(_bound(resolution, base, "L2", source, rule, WHY_GRAPH_BASE))
            continue

        if caller.require_id and caller.require_id in anchors.require_fanout:
            out.extend(
                _fanout_clone(
                    resolution,
                    anchors.require_fanout[caller.require_id],
                    anchors,
                    "require_fanout",
                    WHY_REQUIRE_FANOUT,
                )
            )
            continue

        scope_key = (caller.module_id, caller.caller_var)
        if scope_key[0] and scope_key[1] and scope_key in anchors.scope_fanout:
            out.extend(
                _fanout_clone(
                    resolution,
                    anchors.scope_fanout[scope_key],
                    anchors,
                    "scope_fanout",
                    WHY_SCOPE_FANOUT,
                )
            )
            continue

        js_url = resolution.fact.location.url
        if js_url and anchors.jsurl_bases.get(js_url):
            out.extend(
                _fanout_clone(
                    resolution,
                    anchors.jsurl_bases[js_url],
                    anchors,
                    "file_fanout",
                    WHY_FILE_FANOUT,
                )
            )
            continue

        out.extend(
            _global_pool_resolution(resolution, base, anchors) for base in sorted(pool)
        )

    return tuple(out)


def _normalize_initial(resolution: ApiResolution) -> ApiResolution:
    if resolution.grade == "runtime":
        if resolution.confirmed and not resolution.full_url:
            return replace(resolution, full_url=resolution.confirmed.url)
        return resolution

    if resolution.full_url:
        return resolution

    if resolution.fact.path.startswith("http"):
        return replace(
            resolution,
            grade="full-url",
            full_url=resolution.fact.path,
            base_source="source",
            binding_rule="full_url",
        )
    return resolution


def _bound(
    resolution: ApiResolution,
    base: str,
    grade: ApiGrade,
    base_source: str,
    binding_rule: str,
    why_not: str | None = None,
) -> ApiResolution:
    return replace(
        resolution,
        grade=grade,
        base_url=base,
        full_url=_build_full_url(base, resolution.fact.path),
        base_source=base_source,
        binding_rule=binding_rule,
        why_not_higher_tier=why_not,
    )


def _fanout_clone(
    resolution: ApiResolution,
    bases: set[str],
    anchors: BaseUrlAnchors,
    binding_rule: str,
    why_not: str,
) -> list[ApiResolution]:
    return [
        replace(
            resolution,
            grade="L2",
            base_url=base,
            full_url=_build_full_url(base, resolution.fact.path),
            base_source=anchors.source_of(base) or "fanout",
            binding_rule=binding_rule,
            why_not_higher_tier=why_not,
        )
        for base in sorted(bases)
    ]


def _prod_value(v: ResolvedValue) -> str | None:
    if isinstance(v, Lit):
        return v.value or None
    if isinstance(v, EnvChoice):
        return _prod_value(v.prod)
    return None


def _global_pool_resolution(
    resolution: ApiResolution,
    base: str,
    anchors: BaseUrlAnchors,
) -> ApiResolution:
    src = anchors.source_of(base)

    is_origin_only = base == anchors.origin and not src
    return replace(
        resolution,
        grade="L4" if is_origin_only else "L3",
        base_url=base,
        full_url=_build_full_url(base, resolution.fact.path),
        base_source="origin" if is_origin_only else (src or "fanout"),
        binding_rule="origin_fallback" if is_origin_only else "global_pool",
        why_not_higher_tier=WHY_ORIGIN_FALLBACK if is_origin_only else WHY_GLOBAL_POOL,
    )


def _unique_anchor(caller, anchors: BaseUrlAnchors) -> tuple[str | None, str]:
    if caller.require_id and caller.require_id in anchors.require_base:
        return anchors.require_base[caller.require_id], "require_id"
    scope_key = (caller.module_id, caller.caller_var)
    if scope_key[0] and scope_key[1] and scope_key in anchors.scope_base:
        return anchors.scope_base[scope_key], "module_scope"
    if caller.caller_var and _is_distinctive_handle(caller.caller_var):
        base = anchors.handle_base.get(caller.caller_var)
        if base:
            return base, "distinctive_handle"
    return None, ""


def _bases_by_canonical(
    resolutions: list[ApiResolution],
    client_graph: ClientGraph,
    base_facts: tuple[BaseFact, ...],
    origin: str,
) -> dict[ClientKey, tuple[str, str]]:
    cdp: dict[ClientKey, set[str]] = {}
    for resolution in resolutions:
        if resolution.grade != "runtime" or not resolution.confirmed:
            continue
        base = compute_base_url(resolution.confirmed.url, resolution.fact.path)
        if not base:
            continue
        for ref in resolution.fact.client_refs:
            canon = client_graph.canonical(ref)

            if canon is not None:
                cdp.setdefault(canon, set()).add(base)

    static: dict[ClientKey, set[str]] = {}
    for fact in base_facts:
        value = _prod_value(fact.base_value)
        if not value:
            continue
        base = _absolutize(value, origin)
        for ref in fact.client_refs:
            canon = client_graph.canonical(ref)
            if canon is not None:
                static.setdefault(canon, set()).add(base)

    out: dict[ClientKey, tuple[str, str]] = {}
    for canon, bases in cdp.items():
        clean = {b for b in bases if b}
        if len(clean) == 1:
            out[canon] = (next(iter(clean)), "cdp")
    for canon, bases in static.items():
        if canon in out:
            continue
        clean = {b for b in bases if b}
        if len(clean) == 1:
            out[canon] = (next(iter(clean)), "static")
    return out


def _graph_base(
    resolution: ApiResolution,
    client_graph: ClientGraph,
    base_by_canon: dict[ClientKey, tuple[str, str]],
) -> tuple[str, str, str] | None:
    tmpl = resolution.fact.url_template
    if tmpl is not None and tmpl.segments:
        head = tmpl.segments[0]
        if isinstance(head, RefHole) and head.client_ref is not None:
            canon = client_graph.canonical(head.client_ref)

            if canon is not None and canon in base_by_canon:
                base, source = base_by_canon[canon]
                return base, source, "client_graph_host"

    for ref in resolution.fact.client_refs:
        canon = client_graph.canonical(ref)
        if canon is not None and canon in base_by_canon:
            base, source = base_by_canon[canon]
            return base, source, "client_graph_instance"
    return None
