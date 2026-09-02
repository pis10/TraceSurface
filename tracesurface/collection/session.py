from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from tracesurface.collection.deps import DiscoveryDeps
from tracesurface.collection.discovery.fact_store import FactStore
from tracesurface.config import CollectionSettings
from tracesurface.models import (
    CDPRequest,
    CDPResult,
    ExtractionFacts,
    ScanWarning,
    SecretMatch,
    SourceRef,
)
from tracesurface.policies import TargetContext
from tracesurface.sources import store_source

EventPayload = dict[str, Any]

DiscoveryFingerprint = tuple[int, int, int, int, int, tuple[int, int, int, int, int]]


def _format_cdp_collection_error(payload: EventPayload) -> str:
    detail = str(payload.get("error") or "")

    if len(detail) > 100:
        detail = detail[:99] + "…"
    return f"采集异常：{detail}" if detail else "采集异常"


EVENT_FORMATTERS: dict[str, Callable[[EventPayload], str]] = {
    "recursion_error": (
        lambda payload: f"采集模块 {payload['explorer']} 递归过深，已跳过"
    ),
    "explorer_exception": (
        lambda payload: f"采集模块 {payload['explorer']} 异常，已跳过"
    ),
    "cdp_collection_error": _format_cdp_collection_error,
}


@dataclass(slots=True)
class DiscoveryCache:
    source_scans: dict[str, object] = field(default_factory=dict)
    inline_static_urls: dict[str, set[str]] = field(default_factory=dict)
    harvest: dict[str, set[str]] = field(default_factory=dict)
    validated_attempted: set[str] = field(default_factory=set)
    validated_ok: set[str] = field(default_factory=set)


@dataclass(slots=True)
class DiscoverySession:
    target: TargetContext
    ports: DiscoveryDeps
    settings: CollectionSettings
    scan_id: int | None = None
    hash_prefix: str = ""
    source_scope: int | str = field(default_factory=lambda: f"adhoc-{uuid4().hex}")
    facts: FactStore = field(default_factory=FactStore)
    html_source: SourceRef | None = None
    cdp_requests: list[CDPRequest] = field(default_factory=list)
    cdp_request_keys: set[str] = field(default_factory=set)
    routes_visited: set[str] = field(default_factory=set)
    json_response_bodies: dict[str, str] = field(default_factory=dict)
    cache: DiscoveryCache = field(default_factory=DiscoveryCache)
    warnings: list[ScanWarning] = field(default_factory=list)
    extraction: ExtractionFacts = field(default_factory=ExtractionFacts)
    secrets: list[SecretMatch] = field(default_factory=list)
    _wrapper_gateways: set[str] = field(default_factory=set)
    _wrapper_infixes: dict[str, set[str]] = field(default_factory=dict)

    @property
    def target_url(self) -> str:
        return self.target.policy_url

    @property
    def js_sources(self) -> dict[str, SourceRef]:
        return self.facts.js_sources

    @property
    def js_urls(self) -> set[str]:
        return set(self.facts.js_facts)

    def add_js_urls(
        self,
        urls: Iterable[str],
        *,
        source: str = "unknown",
        evidence_url: str = "",
    ) -> set[str]:
        added: set[str] = set()
        for url in urls or ():
            if self.facts.add_js(url, source=source, evidence_url=evidence_url):
                added.add(url)
        return added

    async def add_js_source(self, url: str, source: str) -> SourceRef:
        existing = self.facts.js_sources.get(url)
        if existing is not None:
            return existing

        ref = await asyncio.to_thread(
            store_source,
            self.source_scope,
            "js",
            url,
            source,
        )
        self.facts.add_js_source(url, ref)
        self._learn_wrappers(source)
        return ref

    def add_cdp_requests(self, requests: Iterable[CDPRequest]) -> int:
        added = 0
        for request in requests:
            if request.dedup_key not in self.cdp_request_keys:
                self.cdp_requests.append(request)
                self.cdp_request_keys.add(request.dedup_key)
                added += 1
        return added

    def add_route_facts(
        self,
        routes: Iterable[str],
        *,
        source: str,
        evidence_url: str = "",
    ) -> set[str]:
        added: set[str] = set()
        for route in routes or ():
            if self.facts.add_route(route, source=source, evidence_url=evidence_url):
                added.add(route)
        return added

    async def add_html_source(
        self,
        url: str,
        html: str,
        *,
        source: str,
        bootstrap: bool = False,
    ) -> bool:
        ref = await asyncio.to_thread(
            store_source,
            self.source_scope,
            "html",
            url,
            html,
        )

        if bootstrap:
            self.html_source = ref
        self._learn_wrappers(html)
        return self.facts.add_html(url, ref, source=source)

    def wrapper_prefixes(self) -> dict[str, str]:
        from tracesurface.extraction.wrappers import finalize_wrapper_prefixes

        if not self._wrapper_gateways:
            return {}
        return finalize_wrapper_prefixes(self._wrapper_infixes, self._wrapper_gateways)

    def absorb_extraction(
        self,
        facts: ExtractionFacts,
        secrets: tuple[SecretMatch, ...] = (),
    ) -> None:
        self.extraction = ExtractionFacts(
            requests=self.extraction.requests + facts.requests,
            bases=self.extraction.bases + facts.bases,
            aliases=self.extraction.aliases + facts.aliases,
        )
        self.secrets.extend(secrets)

    def _learn_wrappers(self, source: str) -> None:
        from tracesurface.extraction.wrappers import gateways_in_calls, infixes_for

        found = gateways_in_calls(source)
        if not found:
            return
        self._wrapper_gateways |= found
        for gw, infs in infixes_for(source, self._wrapper_gateways).items():
            self._wrapper_infixes.setdefault(gw, set()).update(infs)

    def add_mfe_entry_urls(self, urls: Iterable[str]) -> set[str]:
        added: set[str] = set()
        for url in urls or ():
            if self.facts.add_mfe_entry(url):
                added.add(url)
        return added

    def discovery_fingerprint(self) -> DiscoveryFingerprint:
        return (
            len(self.facts.js_facts),
            len(self.facts.js_sources),
            len(self.cdp_request_keys),
            len(self.json_response_bodies),
            len(self.routes_visited),
            self.facts.fingerprint(),
        )

    def record_cdp_diagnostics(
        self,
        phase: str,
        cdp_result: CDPResult,
        **payload,
    ) -> None:
        if cdp_result.collection_error:
            self.record_event(
                "cdp_collection_error",
                phase=phase,
                error=cdp_result.collection_error,
                **payload,
            )

    def record_event(self, kind: str, **payload: Any) -> None:
        formatter = EVENT_FORMATTERS.get(kind)
        if formatter is not None:
            payload.setdefault("target_url", self.target_url)

            try:
                message = formatter(payload)
            except (KeyError, TypeError):
                message = ""
            if message:
                warning = ScanWarning(code=kind, message=message)
                if warning not in self.warnings:
                    self.warnings.append(warning)
