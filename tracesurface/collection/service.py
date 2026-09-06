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
from tracesurface.models import CDPResult, CollectionBundle, ScanWarning
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


def _bootstrap_redirected_away(
    target_url: str,
    effective_url: str,
    cdp_result: CDPResult,
    *,
    pin_navigation: bool,
) -> str | None:
    """首屏是否已失去目标站：返回跳转去向 URL 表示应跳过，None 表示继续采集。

    钉住模式下 page.url 不可信（守卫否决后仍停在原站，也可能在采集间隙
    被跳转逻辑溜走），只认"拦到过跳转且什么同源材料都没采到"；
    非钉住模式按最终落地 URL 判定。
    """
    if pin_navigation:
        if cdp_result.blocked_navigations and not (
            cdp_result.html_content or cdp_result.js_urls or cdp_result.requests
        ):
            return cdp_result.blocked_navigations[0]
        return None
    if not DEFAULT_SETTINGS.collection.redirect_guard_enabled:
        return None
    if is_external_redirect(target_url, effective_url):
        return effective_url
    return None


async def collect_site(
    target_url: str,
    browser,
    wait_ms: int,
    http: HttpTextClient,
    cpu: ProcessPoolExecutor,
    scan_id: int | None = None,
    auth_state: dict[str, Any] | None = None,
    headed: bool = False,
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
    # 无认证扫描钉住目标站：否决主框架跨源导航，防止未登录外跳打断采集；
    # 带登录态或有头模式不钉，SSO 往返可能是认证流程的一部分
    pin_navigation = auth_state is None and not headed
    try:
        cdp_result = await tracer.collect(
            page,
            CDPCollectRequest(
                target_url=target_url,
                wait_ms=wait_ms,
                goto_timeout_ms=DEFAULT_SETTINGS.collection.bootstrap_goto_timeout_ms,
                headed=headed,
                pin_navigation=pin_navigation,
            ),
        )

        html_source = cdp_result.html_content

        effective_url = page.url
        hash_prefix = detect_hash_prefix(effective_url)
        page_url = effective_url.split("?")[0]

        lost_to = _bootstrap_redirected_away(
            target_url,
            effective_url,
            cdp_result,
            pin_navigation=pin_navigation,
        )
        if lost_to is not None:
            reason = "且无同源内容可采集" if pin_navigation else ""
            warning = ScanWarning(
                code="external_redirect_blocked",
                message=(
                    f"首屏跳转站外{reason}，已跳过扫描"
                    f"（{display_origin(target_url)} → {display_origin(lost_to)}）"
                ),
            )
            return CollectionBundle(
                target_url=target_url,
                scan_id=scan_id,
                warnings=(warning,),
                skipped=True,
            )

        if pin_navigation and is_external_redirect(target_url, effective_url):
            # 采集间隙页面被跳转逻辑溜走：材料已在手，目标仍以原始 URL 为准
            page_url = ""
            hash_prefix = ""

        state_target_url = page_url or target_url

        state = DiscoverySession(
            target=TargetContext(state_target_url),
            ports=DiscoveryDeps(
                http=http,
                cpu=cpu,
                page=page,
            ),
            settings=DEFAULT_SETTINGS.collection,
            scan_id=scan_id,
            pin_navigation=pin_navigation,
            hash_prefix=hash_prefix,
            source_scope=scan_id if scan_id is not None else f"adhoc-{uuid4().hex}",
            facts=FactStore(),
            cdp_requests=list(cdp_result.requests),
            cdp_request_keys={r.dedup_key for r in cdp_result.requests},
            json_response_bodies=dict(cdp_result.json_response_bodies),
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

        if cdp_result.blocked_navigations:
            unique_targets = tuple(dict.fromkeys(cdp_result.blocked_navigations))
            state.record_event(
                "navigation_pinned",
                targets=unique_targets[:3],
                count=len(cdp_result.blocked_navigations),
            )

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
