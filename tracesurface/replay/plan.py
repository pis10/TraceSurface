from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.models import (
    CDPReplayTarget,
    Param,
    ReplayCandidate,
    ReplayJob,
    ReplayPlan,
    ReplayRequest,
)
from tracesurface.policies import (
    BODY_METHODS,
    DEFAULT_THIRD_PARTY_DOMAINS,
    ReplaySafetyPolicy,
    ThirdPartyPolicy,
)
from tracesurface.urls import dedup_key


def fill_path(
    url: str,
    *,
    path_fill: str = DEFAULT_SETTINGS.route_materialization.path_fill,
) -> str:
    return url.replace("EXPR", path_fill)


def _fill_value(
    value: Any,
    *,
    param_fill: object = DEFAULT_SETTINGS.route_materialization.param_fill,
) -> Any:
    if value == "?":
        return param_fill

    if isinstance(value, dict):
        return {
            key: _fill_value(item, param_fill=param_fill) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_fill_value(item, param_fill=param_fill) for item in value]
    return value


def fill_params(
    params: Sequence[Param],
    *,
    param_fill: object = DEFAULT_SETTINGS.route_materialization.param_fill,
) -> tuple[dict[str, Any], dict[str, Any]]:
    query: dict[str, Any] = {}
    body: dict[str, Any] = {}
    for param in params:
        value = _fill_value(param.default, param_fill=param_fill)

        bucket = body if param.location == "body" else query
        bucket[param.name] = value
    return query, body


class ReplayPlanBuilder:
    def __init__(
        self,
        *,
        safety: ReplaySafetyPolicy | None = None,
        third_party: ThirdPartyPolicy | None = None,
        path_fill: str = DEFAULT_SETTINGS.route_materialization.path_fill,
        param_fill: object = DEFAULT_SETTINGS.route_materialization.param_fill,
    ) -> None:
        self.safety = safety or ReplaySafetyPolicy()
        self.third_party = third_party or ThirdPartyPolicy(DEFAULT_THIRD_PARTY_DOMAINS)
        self.path_fill = path_fill
        self.param_fill = param_fill

    def build(self, job: ReplayJob) -> ReplayPlan:
        requests: list[ReplayRequest] = []
        seen_keys: set[str] = set()

        for target in job.cdp_requests:
            for req in self.build_cdp_requests(
                target,
                target_url=job.target_url,
                allow_destructive=job.allow_destructive,
            ):
                seen_keys.add(dedup_key(req.method, req.url))
                requests.append(req)

        for cand in job.candidates:
            if not cand.resolution_id or cand.status == "confirmed":
                continue
            for req in self.build_requests(
                cand,
                target_url=job.target_url,
                allow_destructive=job.allow_destructive,
            ):
                if dedup_key(req.method, req.url) in seen_keys:
                    continue
                requests.append(req)
        return ReplayPlan(job.scan_id, job.target_url, tuple(requests))

    def build_cdp_requests(
        self,
        target: CDPReplayTarget,
        *,
        target_url: str = "",
        allow_destructive: bool | None = None,
    ) -> tuple[ReplayRequest, ...]:
        url = target.url or ""
        if not url:
            return ()
        parsed = urlparse(url)

        if not parsed.hostname or parsed.scheme not in ("http", "https"):
            return ()

        if self.third_party.is_third_party(url, target_url):
            return ()
        method = (target.method or "GET").upper()
        safety = self.safety
        if allow_destructive is not None:
            safety = ReplaySafetyPolicy(allow_destructive=allow_destructive)

        if not safety.can_send(method):
            return ()

        has_body = bool(target.raw_body)
        return (
            ReplayRequest(
                resolution_id=target.resolution_id,
                cdp_request_id=target.cdp_request_id,
                method=method,
                url=url,
                query={},
                body=None,
                raw_body=target.raw_body if has_body else None,
                content_type=target.content_type,
                variant="cdp",
                origin="cdp",
            ),
        )

    def build_requests(
        self,
        candidate: ReplayCandidate,
        *,
        target_url: str = "",
        allow_destructive: bool | None = None,
    ) -> tuple[ReplayRequest, ...]:
        resolution_id = candidate.resolution_id
        url_raw = candidate.full_url or ""
        if not url_raw:
            return ()
        parsed_raw = urlparse(url_raw)

        if parsed_raw.netloc and "EXPR" in parsed_raw.netloc:
            return ()
        if not parsed_raw.hostname:
            return ()

        if not parsed_raw.scheme:
            url_raw = "https:" + url_raw
        elif parsed_raw.scheme not in ("http", "https"):
            return ()

        url = fill_path(url_raw, path_fill=self.path_fill)

        if self.third_party.is_third_party(url, target_url):
            return ()

        query, body = fill_params(
            candidate.params,
            param_fill=self.param_fill,
        )
        method = (candidate.method or "UNKNOWN").upper()
        safety = self.safety

        if allow_destructive is not None:
            safety = ReplaySafetyPolicy(allow_destructive=allow_destructive)

        if not safety.can_send(method):
            return ()

        tier = candidate.tier
        bsrc = candidate.base_source
        brule = candidate.binding_rule
        wnht = candidate.why_not_higher_tier

        if method == "UNKNOWN":
            return (
                ReplayRequest(
                    resolution_id=resolution_id,
                    method="GET",
                    url=url,
                    query=dict(query, **body),
                    body=None,
                    variant="GET",
                    inference_tier=tier,
                    base_source=bsrc,
                    binding_rule=brule,
                    why_not_higher_tier=wnht,
                ),
                ReplayRequest(
                    resolution_id=resolution_id,
                    method="POST",
                    url=url,
                    query=query,
                    body=body or {},
                    variant="POST",
                    inference_tier=tier,
                    base_source=bsrc,
                    binding_rule=brule,
                    why_not_higher_tier=wnht,
                ),
            )

        if method in BODY_METHODS:
            return (
                ReplayRequest(
                    resolution_id=resolution_id,
                    method=method,
                    url=url,
                    query=query,
                    body=body or {},
                    inference_tier=tier,
                    base_source=bsrc,
                    binding_rule=brule,
                    why_not_higher_tier=wnht,
                ),
            )

        return (
            ReplayRequest(
                resolution_id=resolution_id,
                method=method,
                url=url,
                query=dict(query, **body),
                body=None,
                inference_tier=tier,
                base_source=bsrc,
                binding_rule=brule,
            ),
        )
