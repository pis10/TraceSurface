from __future__ import annotations

from tracesurface.extraction.analyzer import ASTAnalyzer
from tracesurface.models import (
    BaseFact,
    ClientAliasFact,
    CollectionBundle,
    ExtractionFacts,
    ExtractionResult,
    RequestFact,
)
from tracesurface.secrets.extractor import SecretScanner
from tracesurface.sources import iter_sources


def _learn_wrapper_prefixes(bundle: CollectionBundle) -> dict[str, str]:
    from tracesurface.extraction.wrappers import (
        finalize_wrapper_prefixes,
        gateways_in_calls,
        infixes_for,
    )

    gateways: set[str] = set()
    for _, source in iter_sources(bundle.js_sources):
        gateways |= gateways_in_calls(source)
    if not gateways:
        return {}

    gw_infixes: dict[str, set[str]] = {}
    for _, source in iter_sources(bundle.js_sources):
        for gw, infs in infixes_for(source, gateways).items():
            gw_infixes.setdefault(gw, set()).update(infs)
    return finalize_wrapper_prefixes(gw_infixes, gateways)


def extract_collection(bundle: CollectionBundle) -> ExtractionResult:
    analyzer = ASTAnalyzer()
    f_requests: list[RequestFact] = []
    f_bases: list[BaseFact] = []
    f_aliases: list[ClientAliasFact] = []
    secret_scanner = SecretScanner()

    def absorb(facts: ExtractionFacts) -> None:
        f_requests.extend(facts.requests)
        f_bases.extend(facts.bases)
        f_aliases.extend(facts.aliases)

    wrapper_prefixes = _learn_wrapper_prefixes(bundle)

    js_count = 0
    for js_url, source in iter_sources(bundle.js_sources):
        js_count += 1
        absorb(
            analyzer.analyze_js_all(
                source, js_url=js_url, wrapper_prefixes=wrapper_prefixes
            )
        )
        secret_scanner.scan_js(js_url, source)

    for html_js_url, html_src in iter_sources(bundle.html_pages):
        if not html_src:
            continue
        absorb(
            analyzer.analyze_html_inline_all(
                html_src, js_url=html_js_url, wrapper_prefixes=wrapper_prefixes
            )
        )
        secret_scanner.scan_html(html_js_url, html_src)

    return ExtractionResult(
        secrets=tuple(secret_scanner.results),
        js_count=js_count,
        facts=ExtractionFacts(
            requests=tuple(f_requests),
            bases=tuple(f_bases),
            aliases=tuple(f_aliases),
        ),
    )
