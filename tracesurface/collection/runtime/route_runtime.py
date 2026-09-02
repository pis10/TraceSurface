from __future__ import annotations

from tracesurface.collection.route import build_route_url, fill_dynamic_params
from tracesurface.collection.runtime.cdp_trace import CDPCollectRequest, CDPTraceSession
from tracesurface.collection.session import DiscoverySession
from tracesurface.config import DEFAULT_SETTINGS

_SOURCE_ORDER = {
    "mfe_active_rule": 0,
    "router_table": 1,
    "named_navigation": 2,
    "html_inline_route": 3,
    "w3c_navigation": 4,
}


def _route_sort_key(path: str, source: str) -> tuple[int, int, str]:
    return (_SOURCE_ORDER.get(source, 99), path.count("/"), path)


async def _visit_one(
    state: DiscoverySession,
    fact,
    cdp_tracer: CDPTraceSession,
    page,
) -> None:
    route = fact.path

    fillable = fill_dynamic_params(route)
    route_url = build_route_url(state.target_url, fillable, state.hash_prefix)
    fact.attempts += 1

    pre_js = set(state.js_urls)
    pre_cdp = len(state.cdp_request_keys)
    try:
        cdp = await cdp_tracer.collect(
            page,
            CDPCollectRequest(
                target_url=route_url,
                wait_ms=DEFAULT_SETTINGS.collection.route_total_timeout_ms,
                goto_timeout_ms=DEFAULT_SETTINGS.collection.route_total_timeout_ms,
                total_timeout_ms=DEFAULT_SETTINGS.collection.route_total_timeout_ms,
            ),
        )
    except Exception as exc:
        fact.error = repr(exc)
        state.record_event(
            "cdp_collection_error",
            phase="route",
            route=route,
            route_url=route_url,
            error=fact.error,
        )
        return

    state.record_cdp_diagnostics("route", cdp, route=route, route_url=route_url)

    has_partial_data = bool(cdp.js_urls or cdp.requests or cdp.html_content)

    if cdp.collection_error and not has_partial_data:
        fact.error = cdp.collection_error
        return

    fact.visited = True
    state.routes_visited.add(route)
    state.add_js_urls(cdp.js_urls, source="route_cdp", evidence_url=route_url)
    for js_url, source in cdp.js_sources.items():
        await state.add_js_source(js_url, source)
    state.add_cdp_requests(cdp.requests)

    fact.new_js += len(state.js_urls - pre_js)
    fact.new_cdp += len(state.cdp_request_keys) - pre_cdp
    fact.error = "route_total_timeout" if cdp.timed_out else ""

    if cdp.html_content:
        page_key = route_url.split("#")[0].split("?")[0]
        await state.add_html_source(
            page_key,
            cdp.html_content,
            source="route_rendered",
        )

    for url, body in cdp.json_response_bodies.items():
        state.json_response_bodies.setdefault(url, body)


async def visit_route_facts(state: DiscoverySession) -> None:
    candidates = [
        fact
        for fact in state.facts.route_facts.values()
        if not fact.visited and fact.attempts == 0
    ]
    candidates.sort(key=lambda fact: _route_sort_key(fact.path, fact.source))
    cdp_tracer = CDPTraceSession()

    page = state.ports.page
    if page is None:
        raise RuntimeError("route runtime requires a page")

    for fact in candidates:
        await _visit_one(state, fact, cdp_tracer, page)


class RouteRuntimeExplorer:
    name = "route-runtime"
    run_once = False

    async def discover(
        self,
        session: DiscoverySession,
        round_num: int = 0,
    ) -> None:
        del round_num
        await visit_route_facts(session)
