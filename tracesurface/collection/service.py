from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from tracesurface.collection.deps import DiscoveryDeps, HttpTextClient
from tracesurface.collection.discovery.engine import run_discovery_loop
from tracesurface.collection.discovery.fact_store import FactStore
from tracesurface.collection.runtime.auth import (
    apply_auth_bundle_to_context,
    split_storage_state,
)
from tracesurface.collection.runtime.cdp_trace import CDPCollectRequest, CDPTraceSession
from tracesurface.collection.session import DiscoverySession
from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.models import CollectionBundle, ScanWarning
from tracesurface.policies import TargetContext
from tracesurface.urls import canonical_origin_key


def detect_hash_prefix(page_url: str) -> str:
    if "/#!/" in page_url:
        return "#!/"
    if "/#/" in page_url:
        return "#/"
    return ""


def redirect_guard_origin(url: str) -> tuple[str, int | None]:
    return canonical_origin_key(url)


def display_origin(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port}" if host else ""


def is_external_redirect(requested_url: str, final_url: str) -> bool:
    return redirect_guard_origin(requested_url) != redirect_guard_origin(final_url)


async def collect_site(
    target_url: str,
    browser,
    wait_ms: int,
    http: HttpTextClient,
    cpu: ProcessPoolExecutor,
    scan_id: int | None = None,
    auth_state: dict[str, Any] | None = None,
    headed: bool = False,
    block_redirects: bool = False,
) -> CollectionBundle:
    context_kwargs_base: dict[str, Any] = {
        "user_agent": DEFAULT_SETTINGS.browser.user_agent,
        "ignore_https_errors": DEFAULT_SETTINGS.browser.ignore_https_errors,
    }

    clean_storage, _ = split_storage_state(auth_state)
    ctx_kwargs = dict(context_kwargs_base)
    if clean_storage is not None:
        ctx_kwargs["storage_state"] = clean_storage

    context = await browser.new_context(**ctx_kwargs)
    await apply_auth_bundle_to_context(context, auth_state)
    page = await context.new_page()
    tracer = CDPTraceSession()
    try:
        cdp_result = await tracer.collect(
            page,
            CDPCollectRequest(
                target_url=target_url,
                wait_ms=wait_ms,
                goto_timeout_ms=DEFAULT_SETTINGS.collection.bootstrap_goto_timeout_ms,
                headed=headed,
                block_redirects=block_redirects,
            ),
        )

        html_source = cdp_result.html_content

        effective_url = page.url
        hash_prefix = detect_hash_prefix(effective_url)
        page_url = effective_url.split("?")[0]

        redirect_blocked = (
            DEFAULT_SETTINGS.collection.redirect_guard_enabled
            and not block_redirects
            and is_external_redirect(target_url, effective_url)
        )
        if redirect_blocked:
            warning = ScanWarning(
                code="external_redirect_blocked",
                message=(
                    "首屏跳转站外，已跳过扫描"
                    f"（{display_origin(target_url)} → {display_origin(effective_url)}）"
                ),
            )
            return CollectionBundle(
                target_url=target_url,
                scan_id=scan_id,
                warnings=(warning,),
                skipped=True,
            )

        state_target_url = page_url or target_url

        if block_redirects and cdp_result.blocked_redirects:
            effective_marker = effective_url if effective_url != target_url else ""
            target_ctx = TargetContext(
                requested_url=target_url,
                effective_url=effective_marker or None,
            )
        else:
            effective_marker = ""
            target_ctx = TargetContext(state_target_url)

        state = DiscoverySession(
            target=target_ctx,
            ports=DiscoveryDeps(
                http=http,
                cpu=cpu,
                page=page,
            ),
            settings=DEFAULT_SETTINGS.collection,
            scan_id=scan_id,
            hash_prefix=hash_prefix,
            source_scope=scan_id if scan_id is not None else f"adhoc-{uuid4().hex}",
            facts=FactStore(),
            cdp_requests=list(cdp_result.requests),
            cdp_request_keys={r.dedup_key for r in cdp_result.requests},
            json_response_bodies=dict(cdp_result.json_response_bodies),
        )

        if block_redirects and cdp_result.blocked_redirects:
            state.record_event(
                "redirects_blocked",
                count=cdp_result.blocked_redirects,
                effective_url=effective_marker,
            )

        for js_url in cdp_result.js_urls:
            state.facts.add_js(
                js_url,
                source="bootstrap_cdp",
                evidence_url=page_url,
            )

        if cdp_result.js_sources:
            for js_url in tuple(cdp_result.js_sources):
                await state.add_js_source(
                    js_url,
                    cdp_result.js_sources.pop(js_url),
                )

        if html_source:
            await state.add_html_source(
                page_url or target_url,
                html_source,
                source="bootstrap",
                bootstrap=True,
            )
            cdp_result.html_content = ""
            del html_source

        state.record_cdp_diagnostics("bootstrap", cdp_result, page_url=page_url)

        await run_discovery_loop(state)

        return CollectionBundle(
            target_url=target_url,
            scan_id=scan_id,
            html_pages={
                html_url: fact.ref for html_url, fact in state.facts.html_facts.items()
            },
            js_sources=dict(state.js_sources),
            cdp_requests=tuple(state.cdp_requests),
            route_facts=tuple(state.facts.route_facts.values()),
            extraction=state.extraction,
            secrets=tuple(state.secrets),
            warnings=tuple(state.warnings),
        )
    finally:
        await context.close()
