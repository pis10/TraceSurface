from __future__ import annotations

from urllib.parse import urljoin

from tracesurface.collection.artifacts.chunks.evaluator import ChunkEvaluator
from tracesurface.collection.artifacts.entry_fetcher import MFEEntryFetcher
from tracesurface.collection.artifacts.static_analysis import (
    StaticArtifactResult,
    analyze_html_artifact,
    analyze_js_artifact,
)
from tracesurface.collection.deps import run_cpu
from tracesurface.collection.session import DiscoverySession


class ArtifactExplorer:
    name = "artifact"
    run_once = False

    def __init__(self) -> None:
        self.chunk_evaluator = ChunkEvaluator()
        self.entry_fetcher = MFEEntryFetcher()

    async def discover(
        self,
        session: DiscoverySession,
        round_num: int = 0,
    ) -> None:
        del round_num
        await self.discover_artifacts(session)

    async def discover_artifacts(self, state: DiscoverySession) -> None:
        graph = state.facts
        new_html_items = [
            (url, fact.ref)
            for url, fact in graph.html_facts.items()
            if url not in graph.processed_html_sources
        ]
        new_js_items = [
            (url, ref)
            for url, ref in state.js_sources.items()
            if url not in graph.processed_js_sources
        ]

        prefixes = state.wrapper_prefixes()
        for html_url, ref in new_html_items:
            result = await run_cpu(
                state.ports.cpu,
                analyze_html_artifact,
                ref,
                html_url,
                state.target_url,
                prefixes,
            )
            self._add_result(
                state,
                result,
                evidence_url=html_url,
                js_source="html_asset",
                router_source="html_inline_route",
            )
            state.cache.inline_static_urls[html_url] = set(
                result.inline_static_urls
            )
            state.absorb_extraction(result.extraction, result.secrets)
            await self._evaluate_chunks(state, result)
            graph.processed_html_sources.add(html_url)

        prefixes = state.wrapper_prefixes()
        for js_url, ref in new_js_items:
            result = await run_cpu(
                state.ports.cpu,
                analyze_js_artifact,
                ref,
                js_url,
                state.target_url,
                prefixes,
            )
            self._add_result(
                state,
                result,
                evidence_url=js_url,
                js_source="bundler_runtime",
                router_source="router_table",
            )
            state.cache.source_scans[js_url] = result.source_scan or False
            state.absorb_extraction(result.extraction, result.secrets)
            await self._evaluate_chunks(state, result)
            graph.processed_js_sources.add(js_url)

        from tracesurface.collection.artifacts.micro_frontend.service import (
            collect_micro_frontend,
        )

        await collect_micro_frontend(state)
        await self.entry_fetcher.fetch(state)

    @staticmethod
    def _add_result(
        state: DiscoverySession,
        result: StaticArtifactResult,
        *,
        evidence_url: str,
        js_source: str,
        router_source: str,
    ) -> None:
        state.add_js_urls(
            result.js_urls,
            source=js_source,
            evidence_url=evidence_url,
        )
        state.add_route_facts(
            result.router_routes,
            source=router_source,
            evidence_url=evidence_url,
        )
        state.add_route_facts(
            result.named_routes,
            source="named_navigation",
            evidence_url=evidence_url,
        )
        state.add_route_facts(
            result.w3c_routes,
            source="w3c_navigation",
            evidence_url=evidence_url,
        )

    async def _evaluate_chunks(
        self,
        state: DiscoverySession,
        result: StaticArtifactResult,
    ) -> None:
        urls = set(result.chunk_urls)
        page = state.ports.page
        if page is not None:
            for plan in result.chunk_plans:
                paths = await self.chunk_evaluator.evaluate_loader(
                    page,
                    plan.function,
                    list(plan.params),
                )
                urls.update(
                    _absolute_chunk_url(path, state.target_url) for path in paths
                )

        state.add_js_urls(
            urls,
            source="bundler_runtime",
            evidence_url=state.target_url,
        )


def _absolute_chunk_url(path: str, base_url: str) -> str:
    if path.startswith("http"):
        return path.split("?", 1)[0]
    return urljoin(base_url + "/", path).split("?", 1)[0]
