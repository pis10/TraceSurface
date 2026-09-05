from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Response
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tracesurface.collection.runtime.request_classifier import (
    RequestClassifier,
    is_business_js,
)
from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.models import CDPRequest, CDPResult, StackFrame
from tracesurface.policies import ResponseCapturePolicy, TargetContext
from tracesurface.urls import canonical_origin_key

_FINALIZE_TIMEOUT_S = 2.0

_BLOCKED_REDIRECT_STATUS = 204


async def _fetch_continue(client: Any, request_id: str) -> None:
    with suppress(PlaywrightError):
        await client.send("Fetch.continueRequest", {"requestId": request_id})


async def _fetch_fulfill_empty(client: Any, request_id: str) -> None:
    with suppress(PlaywrightError):
        await client.send(
            "Fetch.fulfillRequest",
            {
                "requestId": request_id,
                "responseCode": _BLOCKED_REDIRECT_STATUS,
            },
        )


def _expand_stack(stack: dict[str, Any]) -> list[StackFrame]:
    frames = []
    current = stack
    while current:
        for frame in current.get("callFrames", []):
            url = frame.get("url", "")
            if not url or url.startswith("chrome://"):
                continue
            frames.append(
                StackFrame(
                    url=url.split("?")[0],
                    func=frame.get("functionName", "") or "(匿名)",
                    line=frame.get("lineNumber", 0),
                    col=frame.get("columnNumber", 0),
                )
            )
        current = current.get("parent")
    return frames


@dataclass(frozen=True, slots=True)
class CDPCollectRequest:
    target_url: str
    wait_ms: int
    goto_timeout_ms: int
    total_timeout_ms: int | None = None
    headed: bool = False
    block_redirects: bool = False


def _is_redirect(status: int | None) -> bool:
    return status is not None and 300 <= status < 400


def _location_of(headers: dict[str, Any]) -> str:
    for key, value in headers.items():
        if key.lower() == "location":
            return str(value)
    return ""


def _resolve_redirect_target(location: str, base_url: str) -> str:
    try:
        return urljoin(base_url, location)
    except ValueError:
        return location


class CDPTraceSession:
    def __init__(self) -> None:
        self.request_classifier = RequestClassifier()
        self.capture_policy = ResponseCapturePolicy()

    async def collect(self, page: Page, request: CDPCollectRequest) -> CDPResult:
        loop = asyncio.get_running_loop()
        deadline_at = (
            loop.time() + max(0, request.total_timeout_ms) / 1000
            if request.total_timeout_ms is not None
            else None
        )
        target_context = TargetContext(requested_url=request.target_url)
        classifier = self.request_classifier
        capture = self.capture_policy

        def remaining_ms(cap_ms: int) -> int:
            if deadline_at is None:
                return max(1, cap_ms)
            remaining = max(0, int((deadline_at - loop.time()) * 1000))
            return max(1, min(cap_ms, remaining))

        js_urls: set[str] = set()
        js_sources: dict[str, str] = {}
        script_responses: dict[str, Response] = {}
        requests: list[CDPRequest] = []
        requests_by_id: dict[str, CDPRequest] = {}
        text_body_pending: set[str] = set()
        finished_body_ids: set[str] = set()
        json_response_bodies: dict[str, str] = {}
        timed_out = False
        collection_error = ""
        navigation_ok = True
        html_content = ""
        blocked_redirect_count = 0
        target_origin = canonical_origin_key(request.target_url)

        def on_script_response(response: Response) -> None:
            if response.request.resource_type != "script":
                return
            if not 200 <= response.status < 300:
                return
            clean_url = response.url.split("?", 1)[0]
            if not is_business_js(clean_url, target_context, classifier.third_party):
                return
            js_urls.add(clean_url)
            script_responses[clean_url] = response

        client = await page.context.new_cdp_session(page)
        page.on("response", on_script_response)
        try:
            await client.send("Network.enable")
            await client.send("Debugger.enable")
            await client.send(
                "Debugger.setAsyncCallStackDepth",
                {"maxDepth": DEFAULT_SETTINGS.collection.cdp_stack_depth},
            )

            if request.block_redirects:
                await client.send(
                    "Fetch.enable",
                    {
                        "patterns": [
                            {"urlPattern": "*", "requestStage": "Request"},
                            {"urlPattern": "*", "requestStage": "Response"},
                        ]
                    },
                )
            def on_request(params: dict[str, Any]) -> None:
                request_data = params["request"]
                url = request_data["url"]
                request_type = params.get("type", "")
                classification = classifier.classify_cdp_request(
                    url,
                    request_type,
                    target_context,
                )

                if classification.is_js:
                    clean_url = url.split("?", 1)[0]
                    if is_business_js(
                        clean_url,
                        target_context,
                        classifier.third_party,
                    ):
                        js_urls.add(clean_url)
                    return

                if not classification.keep:
                    return

                stack = params.get("initiator", {}).get("stack")
                if not stack:
                    return

                parsed = urlparse(url)
                headers = request_data.get("headers", {}) or {}
                cdp_request = CDPRequest(
                    request_url=url,
                    request_path=parsed.path,
                    method=request_data["method"],
                    query_string=parsed.query,
                    post_data=request_data.get("postData", ""),
                    content_type=headers.get("Content-Type", "")
                    or headers.get("content-type", ""),
                    frames=_expand_stack(stack),
                    request_headers=dict(headers),
                )
                requests.append(cdp_request)
                request_id = params.get("requestId")
                if request_id:
                    requests_by_id[request_id] = cdp_request

            def on_response(params: dict[str, Any]) -> None:
                if params.get("type", "") not in ("Fetch", "XHR"):
                    return
                request_id = params.get("requestId")
                if not request_id or request_id not in requests_by_id:
                    return

                response = params.get("response", {})
                cdp_request = requests_by_id[request_id]
                cdp_request.response_status = response.get("status") or None
                headers = response.get("headers", {}) or {}
                cdp_request.response_headers = dict(headers)
                if capture.is_text_mime(response.get("mimeType", "")):
                    text_body_pending.add(request_id)

            def on_loading_finished(params: dict[str, Any]) -> None:
                request_id = params.get("requestId")
                if not request_id or request_id not in text_body_pending:
                    return
                text_body_pending.discard(request_id)
                if params.get("encodedDataLength", 0) <= capture.body_capture_limit:
                    finished_body_ids.add(request_id)

            client.on("Network.requestWillBeSent", on_request)
            client.on("Network.responseReceived", on_response)
            client.on("Network.loadingFinished", on_loading_finished)

            def on_fetch_paused(params: dict[str, Any]) -> None:
                nonlocal blocked_redirect_count
                request_id = params.get("requestId", "")
                paused = params.get("request", {}) or {}
                paused_url = paused.get("url", "")

                response_status_line = params.get("responseStatusCode")
                is_response_stage = response_status_line is not None

                if not is_response_stage:
                    if (
                        params.get("resourceType") == "Document"
                        and canonical_origin_key(paused_url) != target_origin
                    ):
                        blocked_redirect_count += 1
                        asyncio.create_task(
                            _fetch_fulfill_empty(client, request_id)
                        )
                        return

                    asyncio.create_task(_fetch_continue(client, request_id))
                    return

                response_headers = dict(params.get("responseHeaders") or {})
                if _is_redirect(int(response_status_line)):
                    location = _location_of(response_headers)
                    redirect_to = (
                        _resolve_redirect_target(location, paused_url)
                        if location
                        else ""
                    )
                    if redirect_to and canonical_origin_key(redirect_to) != target_origin:
                        blocked_redirect_count += 1
                        asyncio.create_task(
                            _fetch_fulfill_empty(client, request_id)
                        )
                        return

                asyncio.create_task(_fetch_continue(client, request_id))

            if request.block_redirects:
                client.on("Fetch.requestPaused", on_fetch_paused)

            try:
                await page.goto(
                    request.target_url,
                    wait_until="domcontentloaded",
                    timeout=remaining_ms(request.goto_timeout_ms),
                )
            except PlaywrightTimeoutError as exc:
                if request.total_timeout_ms is None:
                    raise
                navigation_ok = False
                timed_out = True
                collection_error = repr(exc)
            except PlaywrightError as exc:
                if request.total_timeout_ms is None:
                    raise
                navigation_ok = False
                collection_error = repr(exc)

            if navigation_ok:
                if request.headed:
                    if request.wait_ms > 0:
                        await asyncio.sleep(request.wait_ms / 1000)
                elif request.wait_ms > 0:
                    wait_timeout_ms = remaining_ms(request.wait_ms)
                    try:
                        await page.wait_for_load_state(
                            "networkidle",
                            timeout=wait_timeout_ms,
                        )
                    except PlaywrightTimeoutError:
                        if request.total_timeout_ms is not None:
                            timed_out = True
                    except PlaywrightError as exc:
                        if request.total_timeout_ms is None:
                            raise
                        collection_error = collection_error or repr(exc)

            async def capture_script(url: str, response: Response) -> None:
                try:
                    js_sources[url] = await response.text()
                except PlaywrightError:
                    return

            async def capture_request_body(request_id: str) -> None:
                cdp_request = requests_by_id.get(request_id)
                if cdp_request is None:
                    return
                try:
                    result = await client.send(
                        "Network.getResponseBody",
                        {"requestId": request_id},
                    )
                except PlaywrightError:
                    return
                if result.get("base64Encoded"):
                    return
                body = result.get("body", "")
                if len(body) > capture.body_capture_limit:
                    return
                cdp_request.response_body = body
                cdp_request.response_size = len(
                    body.encode("utf-8", errors="replace")
                )
                content_type = next(
                    (
                        str(value).lower()
                        for key, value in cdp_request.response_headers.items()
                        if key.lower() == "content-type"
                    ),
                    "",
                )
                if "json" in content_type:
                    json_response_bodies[
                        cdp_request.request_url.split("?", 1)[0]
                    ] = body

            async def capture_html() -> None:
                nonlocal collection_error, html_content
                if not navigation_ok:
                    return
                try:
                    html_content = await page.content()
                except PlaywrightError as exc:
                    collection_error = collection_error or repr(exc)

            finalizers = [
                capture_script(url, response)
                for url, response in script_responses.items()
            ]
            finalizers.extend(
                capture_request_body(request_id)
                for request_id in finished_body_ids
            )
            finalizers.append(capture_html())
            try:
                await asyncio.wait_for(
                    asyncio.gather(*finalizers),
                    timeout=_FINALIZE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                if request.total_timeout_ms is not None:
                    timed_out = True
        finally:
            page.remove_listener("response", on_script_response)
            if request.block_redirects:
                with suppress(PlaywrightError):
                    await client.send("Fetch.disable")
            with suppress(PlaywrightError):
                await client.detach()

        seen: set[str] = set()
        unique_requests: list[CDPRequest] = []
        for cdp_request in requests:
            if cdp_request.dedup_key in seen:
                continue
            seen.add(cdp_request.dedup_key)
            unique_requests.append(cdp_request)

        return CDPResult(
            target_url=request.target_url,
            js_urls=js_urls,
            js_sources=js_sources,
            requests=unique_requests,
            html_content=html_content,
            json_response_bodies=json_response_bodies,
            timed_out=timed_out,
            collection_error=collection_error,
            blocked_redirects=blocked_redirect_count,
        )
