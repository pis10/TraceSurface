from __future__ import annotations

from dataclasses import dataclass, field

from tracesurface.collection import route
from tracesurface.collection.artifacts.chunks.types import (
    ChunkEvalPlan,
    SourceDocument,
)
from tracesurface.collection.artifacts.chunks.vite import discover_vite_urls
from tracesurface.collection.artifacts.chunks.webpack import build_webpack_eval_plan
from tracesurface.collection.artifacts.html import extract_html_js_urls
from tracesurface.collection.artifacts.micro_frontend.harvest import (
    detect_static_script_urls,
)
from tracesurface.collection.artifacts.micro_frontend.scanner import (
    SourceScan,
    scan_source_tree,
)
from tracesurface.extraction.analyzer import ASTAnalyzer
from tracesurface.htmlast import extract_inline_scripts
from tracesurface.jsast import JsParser
from tracesurface.models import ExtractionFacts, SecretMatch, SourceRef
from tracesurface.secrets.extractor import SecretScanner
from tracesurface.sources import load_source


@dataclass(frozen=True, slots=True)
class StaticArtifactResult:
    js_urls: frozenset[str] = field(default_factory=frozenset)
    router_routes: frozenset[str] = field(default_factory=frozenset)
    named_routes: frozenset[str] = field(default_factory=frozenset)
    w3c_routes: frozenset[str] = field(default_factory=frozenset)
    inline_static_urls: frozenset[str] = field(default_factory=frozenset)
    chunk_urls: frozenset[str] = field(default_factory=frozenset)
    chunk_plans: tuple[ChunkEvalPlan, ...] = ()
    source_scan: SourceScan | None = None
    extraction: ExtractionFacts = field(default_factory=ExtractionFacts)
    secrets: tuple[SecretMatch, ...] = ()


def analyze_html_artifact(
    ref: SourceRef,
    html_url: str,
    target_url: str,
    wrapper_prefixes: dict[str, str] | None = None,
) -> StaticArtifactResult:
    html = load_source(ref)
    router_routes: set[str] = set()
    named_routes: set[str] = set()
    w3c_routes: set[str] = set()
    inline_static_urls: set[str] = set()
    chunk_urls: set[str] = set()
    chunk_plans: list[ChunkEvalPlan] = []
    parser = JsParser()
    analyzer = ASTAnalyzer()
    extraction = ExtractionFacts()

    for start_line, script in extract_inline_scripts(html):
        try:
            norm = parser.normalize(script)
            tree = parser.parse(norm)
            root = tree.root_node
        except Exception:
            continue

        inline_router, inline_named, inline_w3c = route.extract_route_sets_from_tree(
            root
        )
        router_routes |= inline_router
        named_routes |= inline_named
        w3c_routes |= inline_w3c
        inline_static_urls |= detect_static_script_urls(root)

        source = SourceDocument("", script, root)
        chunk_urls.update(discover_vite_urls(source, target_url))
        plan = build_webpack_eval_plan(source)
        if plan is not None:
            chunk_plans.append(plan)

        file_facts = analyzer.analyze_parsed(
            root,
            norm,
            html_url,
            wrapper_prefixes,
            line_offset=start_line,
        )
        extraction = ExtractionFacts(
            requests=extraction.requests + file_facts.requests,
            bases=extraction.bases + file_facts.bases,
            aliases=extraction.aliases + file_facts.aliases,
        )

    secrets = tuple(SecretScanner().scan_html(html_url, html))
    return StaticArtifactResult(
        js_urls=frozenset(extract_html_js_urls(html, html_url)),
        router_routes=frozenset(router_routes),
        named_routes=frozenset(named_routes),
        w3c_routes=frozenset(w3c_routes),
        inline_static_urls=frozenset(inline_static_urls),
        chunk_urls=frozenset(chunk_urls),
        chunk_plans=tuple(chunk_plans),
        extraction=extraction,
        secrets=secrets,
    )


def analyze_js_artifact(
    ref: SourceRef,
    source_url: str,
    target_url: str,
    wrapper_prefixes: dict[str, str] | None = None,
) -> StaticArtifactResult:
    source_text = load_source(ref)
    parser = JsParser()
    try:
        norm = parser.normalize(source_text)
        tree = parser.parse(norm)
        root = tree.root_node
    except Exception:
        return StaticArtifactResult()

    router_routes, named_routes, w3c_routes = route.extract_route_sets_from_tree(root)
    source = SourceDocument(source_url, source_text, root)
    plan = build_webpack_eval_plan(source)
    extraction = ASTAnalyzer().analyze_parsed(
        root, norm, source_url, wrapper_prefixes
    )
    secrets = tuple(SecretScanner().scan_js(source_url, source_text))

    return StaticArtifactResult(
        router_routes=frozenset(router_routes),
        named_routes=frozenset(named_routes),
        w3c_routes=frozenset(w3c_routes),
        chunk_urls=discover_vite_urls(source, target_url),
        chunk_plans=(plan,) if plan is not None else (),
        source_scan=scan_source_tree(root),
        extraction=extraction,
        secrets=secrets,
    )
