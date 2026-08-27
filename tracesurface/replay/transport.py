from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.frozen import to_jsonable
from tracesurface.http import StatelessAsyncClient
from tracesurface.models import ReplayRecord, ReplayRequest
from tracesurface.policies import ResponseCapturePolicy

_RESPONSE_CAPTURE_POLICY = ResponseCapturePolicy(
    DEFAULT_SETTINGS.replay.response_body_capture_limit,
)


def build_headers(referer: str, *, content_type: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": DEFAULT_SETTINGS.browser.user_agent,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


async def _read_limited_body(
    resp: httpx.Response,
    *,
    limit: int = DEFAULT_SETTINGS.replay.response_body_capture_limit,
) -> tuple[bytes, int]:
    content_length = resp.headers.get("Content-Length")
    declared_len: int | None = None
    if content_length:
        try:
            declared_len = int(content_length)
        except ValueError:
            declared_len = None

    chunks: list[bytes] = []
    bytes_seen = 0
    captured_len = 0
    truncated = False

    async for chunk in resp.aiter_bytes():
        if not chunk:
            continue

        remaining = limit - captured_len
        if remaining > 0:
            piece = chunk[:remaining]
            chunks.append(piece)
            captured_len += len(piece)
        bytes_seen += len(chunk)

        if bytes_seen > limit:
            truncated = True
            break

    captured = b"".join(chunks)

    if declared_len is not None:
        if truncated:
            return captured, max(declared_len, limit + 1)
        return captured, max(declared_len, bytes_seen)

    if truncated:
        return captured, limit + 1
    return captured, bytes_seen


def _decode_body(
    resp: httpx.Response,
    normalized_ct: str,
    captured: bytes,
) -> tuple[str | None, bytes | None]:
    if normalized_ct == "bin":
        return None, captured
    return captured.decode(resp.encoding or "utf-8", errors="replace"), None


class HTTPTransport:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        response_body_limit: int = DEFAULT_SETTINGS.replay.response_body_capture_limit,
    ) -> None:
        self.client = client
        self.response_body_limit = response_body_limit

    @staticmethod
    def create_client(
        *,
        timeout: float = DEFAULT_SETTINGS.replay.timeout_s,
        max_redirects: int = DEFAULT_SETTINGS.replay.max_redirects,
        verify: bool = DEFAULT_SETTINGS.http.tls_verify,
        max_connections: int = DEFAULT_SETTINGS.replay.concurrency,
    ) -> httpx.AsyncClient:
        concurrency = max(1, max_connections)
        return StatelessAsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=max_redirects,
            verify=verify,
            limits=httpx.Limits(
                max_connections=concurrency,
                max_keepalive_connections=concurrency,
            ),
        )

    async def send(
        self,
        request: ReplayRequest,
        *,
        scan_id: int,
        referer: str,
    ) -> ReplayRecord:
        has_raw = request.raw_body is not None
        has_body = request.body is not None
        if has_raw:
            content_type = request.content_type
        elif has_body:
            content_type = "application/json"
        else:
            content_type = None
        headers = build_headers(referer, content_type=content_type)
        domain = urlparse(request.url).hostname or ""

        query_payload = to_jsonable(request.query)
        body_payload = to_jsonable(request.body)
        sent_query_json = (
            json.dumps(query_payload, ensure_ascii=False) if request.query else None
        )

        if has_raw:
            sent_body_json = request.raw_body
        elif has_body:
            sent_body_json = json.dumps(body_payload, ensure_ascii=False)
        else:
            sent_body_json = None
        sent_headers_json = json.dumps(headers, ensure_ascii=False)

        send_kwargs: dict[str, Any] = {
            "params": query_payload or None,
            "headers": headers,
        }
        if has_raw:
            send_kwargs["content"] = (request.raw_body or "").encode(
                "utf-8", errors="replace"
            )
        elif has_body:
            send_kwargs["json"] = body_payload

        start = time.perf_counter()
        try:
            async with self.client.stream(
                request.method,
                request.url,
                **send_kwargs,
            ) as resp:
                body_raw, resp_len = await _read_limited_body(
                    resp,
                    limit=self.response_body_limit,
                )
                elapsed_ms = int((time.perf_counter() - start) * 1000)

                normalized_ct = _RESPONSE_CAPTURE_POLICY.normalize_content_type(
                    resp.headers.get("Content-Type", "")
                )
                body_text, body_bytes = _decode_body(resp, normalized_ct, body_raw)

                return ReplayRecord(
                    resolution_id=request.resolution_id,
                    scan_id=scan_id,
                    domain=domain,
                    variant=request.variant,
                    sent_url=request.url,
                    sent_method=request.method,
                    sent_query=sent_query_json,
                    sent_body=sent_body_json,
                    sent_headers=sent_headers_json,
                    status=resp.status_code,
                    resp_headers=json.dumps(dict(resp.headers), ensure_ascii=False),
                    resp_ct=normalized_ct,
                    resp_len=resp_len,
                    resp_body=body_text,
                    resp_bytes=body_bytes,
                    time_ms=elapsed_ms,
                    error=None,
                    inference_tier=request.inference_tier or None,
                    base_source=request.base_source,
                    binding_rule=request.binding_rule,
                    cdp_request_id=request.cdp_request_id,
                )

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return ReplayRecord(
                resolution_id=request.resolution_id,
                scan_id=scan_id,
                domain=domain,
                variant=request.variant,
                sent_url=request.url,
                sent_method=request.method,
                sent_query=sent_query_json,
                sent_body=sent_body_json,
                sent_headers=sent_headers_json,
                status=None,
                resp_headers=None,
                resp_ct="",
                resp_len=0,
                resp_body=None,
                resp_bytes=None,
                time_ms=elapsed_ms,
                error=f"{type(exc).__name__}: {exc}",
                inference_tier=request.inference_tier or None,
                base_source=request.base_source,
                binding_rule=request.binding_rule,
                why_not_higher_tier=request.why_not_higher_tier,
                cdp_request_id=request.cdp_request_id,
            )
