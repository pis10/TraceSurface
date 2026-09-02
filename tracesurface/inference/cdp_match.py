from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from urllib.parse import urlparse

from tracesurface.models import (
    ApiResolution,
    CallerInfo,
    CDPRequest,
    ConfirmedRequest,
    Lit,
    RequestFact,
    SourceLocation,
    UrlTemplate,
)
from tracesurface.urls import dedup_key


@dataclass(frozen=True, slots=True)
class CDPASTMatchResult:
    resolutions: tuple[ApiResolution, ...]
    cdp_only: tuple[CDPRequest, ...]


def match_cdp_ast(
    cdp_requests: list[CDPRequest],
    request_facts: list[RequestFact],
) -> CDPASTMatchResult:
    facts_by_url: dict[str, list[RequestFact]] = {}
    for rf in request_facts:
        facts_by_url.setdefault(rf.location.url, []).append(rf)

    confirmed: dict[tuple[str, int, int], CDPRequest] = {}
    matched_req_keys: set[str] = set()

    for req in cdp_requests:
        for frame in req.frames:
            candidates = facts_by_url.get(frame.url, [])
            for rf in candidates:
                loc = rf.location

                if loc.line == frame.line and loc.col_start <= frame.col < loc.col_end:
                    key = (loc.url, loc.line, loc.col_start)
                    confirmed.setdefault(key, req)

                    matched_req_keys.add(req.dedup_key)

    resolutions: list[ApiResolution] = []
    for rf in request_facts:
        loc = rf.location
        key = (loc.url, loc.line, loc.col_start)
        req = confirmed.get(key)
        if req is None:
            resolutions.append(ApiResolution(fact=rf, grade="no-url"))
            continue

        request = ConfirmedRequest(
            method=req.method,
            url=req.request_url,
            path=req.request_path,
        )
        resolutions.append(
            ApiResolution(
                fact=rf,
                grade="runtime",
                full_url=req.request_url or None,
                confirmed=request,
            )
        )

    cdp_only = tuple(r for r in cdp_requests if r.dedup_key not in matched_req_keys)
    return CDPASTMatchResult(
        resolutions=tuple(resolutions),
        cdp_only=cdp_only,
    )


def merge_runtime_apis(
    resolutions: tuple[ApiResolution, ...],
    cdp_only: Sequence[CDPRequest],
) -> tuple[ApiResolution, ...]:
    if not cdp_only:
        return resolutions

    merged = list(resolutions)
    index_by_key: dict[str, int] = {}
    for i, resolution in enumerate(merged):
        url = resolution.full_url
        if not url:
            continue
        index_by_key.setdefault(
            dedup_key(resolution.fact.method or "UNKNOWN", url),
            i,
        )

    for req in cdp_only:
        if not req.request_url:
            continue
        method = (req.method or "GET").upper()
        key = dedup_key(method, req.request_url)
        confirmed = ConfirmedRequest(
            method=method,
            url=req.request_url,
            path=req.request_path,
        )
        idx = index_by_key.get(key)
        if idx is not None:
            current = merged[idx]
            if current.grade != "runtime" or current.confirmed is None:
                merged[idx] = replace(
                    current,
                    grade="runtime",
                    confirmed=confirmed,
                    full_url=req.request_url,
                )
            continue
        index_by_key[key] = len(merged)
        merged.append(_runtime_resolution(req, confirmed))
    return tuple(merged)


def _runtime_resolution(
    req: CDPRequest,
    confirmed: ConfirmedRequest,
) -> ApiResolution:
    method = confirmed.method
    frame = req.frames[0] if req.frames else None
    parsed_path = urlparse(req.request_url).path or "/"
    path = req.request_path or parsed_path
    location = SourceLocation(
        url=frame.url if frame else "",
        line=frame.line if frame else 0,
        col_start=frame.col if frame else 0,
        col_end=(frame.col + 1) if frame else 1,
    )
    return ApiResolution(
        fact=RequestFact(
            request_id=f"cdp:{req.dedup_key}",
            method=method,
            path=path,
            url_template=UrlTemplate((Lit(path),)),
            client_refs=(),
            params=(),
            location=location,
            caller=CallerInfo(),
            pattern="cdp",
        ),
        grade="runtime",
        full_url=req.request_url,
        confirmed=confirmed,
        base_source="cdp",
        binding_rule="runtime",
    )
