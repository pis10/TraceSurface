from __future__ import annotations

from tracesurface.inference.base_url import (
    build_base_url_anchors,
    dedup_in_scan,
    propagate_methods,
)
from tracesurface.inference.cdp_match import match_cdp_ast, merge_runtime_apis
from tracesurface.inference.client_graph import ClientGraph
from tracesurface.inference.resolve_graph import resolve_graph
from tracesurface.models import CollectionBundle, InferenceResult, ScanResult
from tracesurface.urls import origin_of


def infer(bundle: CollectionBundle) -> InferenceResult:
    facts = bundle.extraction
    origin = origin_of(bundle.target_url)

    matched = match_cdp_ast(
        list(bundle.cdp_requests),
        list(facts.requests),
    )
    confirmed = [r for r in matched.resolutions if r.grade == "runtime"]

    anchors = build_base_url_anchors(
        confirmed,
        base_facts=tuple(facts.bases),
        origin=origin,
    )

    client_graph = ClientGraph.build(facts.aliases)
    resolved = resolve_graph(
        matched.resolutions,
        anchors,
        client_graph,
        facts.bases,
        origin,
    )

    propagated = propagate_methods(resolved, confirmed)
    resolutions = merge_runtime_apis(dedup_in_scan(propagated), matched.cdp_only)
    route_facts = tuple(bundle.route_facts)
    result = ScanResult(
        target_url=bundle.target_url,
        js_count=len(bundle.js_sources),
        cdp_request_count=len(bundle.cdp_requests),
        ast_total=len(facts.requests),
        route_count=len(route_facts),
        visited_route_count=sum(1 for fact in route_facts if fact.visited),
        productive_route_count=sum(1 for fact in route_facts if fact.productive),
        apis=resolutions,
        all_cdp_requests=tuple(bundle.cdp_requests),
        secrets=tuple(bundle.secrets),
        warnings=tuple(bundle.warnings),
    )
    return InferenceResult(result=result)
