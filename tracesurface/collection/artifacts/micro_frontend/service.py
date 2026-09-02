from __future__ import annotations

from urllib.parse import urlparse

from tracesurface.collection.artifacts.micro_frontend.common import is_strict_identifier
from tracesurface.collection.artifacts.micro_frontend.entries import (
    classify_app_entries,
)
from tracesurface.collection.artifacts.micro_frontend.harvest import (
    harvest_identifiers,
)
from tracesurface.collection.artifacts.micro_frontend.qiankun_manifest import (
    match_qiankun_bodies,
)
from tracesurface.collection.artifacts.micro_frontend.scanner import (
    SourceScan,
    enrich_identifiers_from_cached_arrays,
)
from tracesurface.collection.artifacts.micro_frontend.script_loader import (
    render_loader_urls,
    validate_urls,
)
from tracesurface.collection.artifacts.static_analysis import (
    analyze_html_artifact,
    analyze_js_artifact,
)
from tracesurface.collection.deps import run_cpu
from tracesurface.config import DEFAULT_SETTINGS

AppConfig = dict[str, object]


async def collect_micro_frontend(state) -> None:
    register_calls = []
    all_loaders = []
    static_src_urls: set[str] = set()
    source_scans: list[SourceScan] = []
    for js_url, ref in state.js_sources.items():
        cached = state.cache.source_scans.get(js_url)
        if isinstance(cached, SourceScan):
            scan = cached
        elif cached is False:
            continue
        else:
            result = await run_cpu(
                state.ports.cpu,
                analyze_js_artifact,
                ref,
                js_url,
                state.target_url,
            )
            scan = result.source_scan
            if scan is None:
                state.cache.source_scans[js_url] = False
                continue
            state.cache.source_scans[js_url] = scan

        source_scans.append(scan)
        register_calls.extend(scan.register_calls)
        all_loaders.extend(scan.loaders)
        static_src_urls |= scan.static_src_urls

    html_inputs = {}
    if state.html_source:
        html_inputs[state.target_url] = state.html_source
    for html_key, fact in state.facts.html_facts.items():
        html_inputs.setdefault(html_key, fact.ref)

    for html_key, html_ref in html_inputs.items():
        cached_html = state.cache.inline_static_urls.get(html_key)
        if isinstance(cached_html, set):
            static_src_urls |= cached_html
        else:
            result = await run_cpu(
                state.ports.cpu,
                analyze_html_artifact,
                html_ref,
                html_key,
                state.target_url,
            )
            html_urls = set(result.inline_static_urls)
            state.cache.inline_static_urls[html_key] = html_urls
            static_src_urls |= html_urls

    apps_from_register: list[AppConfig] = []
    for rc in register_calls:
        apps_from_register.extend(rc.apps)

    loader_names = {L.func_name for L in all_loaders}
    merged_sites: dict[str, set[str]] = {n: set() for n in loader_names}
    if loader_names:
        for scan in source_scans:
            for name in loader_names:
                merged_sites[name] |= scan.call_sites.get(name, set())

    response_bodies = tuple(state.json_response_bodies.values())
    apps_from_qiankun = (
        await run_cpu(state.ports.cpu, match_qiankun_bodies, response_bodies)
        if response_bodies
        else []
    )

    seen_names: set[str] = set()
    combined_apps: list[AppConfig] = []
    for app in apps_from_register + apps_from_qiankun:
        name = app.get("name")
        if isinstance(name, str) and name and name not in seen_names:
            seen_names.add(name)
            combined_apps.append(app)

    js_from_ab, prefixes_from_ab, html_entries_from_ab = classify_app_entries(
        combined_apps,
        state.target_url,
    )
    state.add_js_urls(js_from_ab, source="mfe_entry_js", evidence_url=state.target_url)
    state.add_route_facts(
        prefixes_from_ab,
        source="mfe_active_rule",
        evidence_url=state.target_url,
    )
    state.add_mfe_entry_urls(html_entries_from_ab)

    harvested_identifiers: set[str] = set()
    if all_loaders and (static_src_urls or state.json_response_bodies):
        harvested_identifiers = await harvest_identifiers(
            ast_static_urls=static_src_urls,
            cdp_response_bodies=state.json_response_bodies,
            target_url=state.target_url,
            http_client=state.ports.http,
            cpu=state.ports.cpu,
            cache=state.cache.harvest,
        )

    ab_names: set[str] = set()
    for app in combined_apps:
        name = app.get("name")
        if isinstance(name, str) and is_strict_identifier(name):
            ab_names.add(name)

    combined_external_ids = harvested_identifiers | ab_names
    if len(combined_external_ids) > DEFAULT_SETTINGS.collection.mfe_max_external_ids:
        combined_external_ids = set(
            sorted(combined_external_ids)[
                : DEFAULT_SETTINGS.collection.mfe_max_external_ids
            ]
        )

    if all_loaders and (any(merged_sites.values()) or combined_external_ids):
        unique_loaders = list(
            {(L.func_name, L.template): L for L in all_loaders}.values()
        )

        if combined_external_ids:
            for loader in unique_loaders:
                merged_sites[loader.func_name] |= combined_external_ids

        for loader in unique_loaders:
            seeds = merged_sites.get(loader.func_name, set())
            if len(seeds) < 2:
                continue
            for scan in source_scans:
                enriched = enrich_identifiers_from_cached_arrays(
                    scan.string_arrays,
                    seeds,
                )
                if enriched:
                    merged_sites[loader.func_name] |= enriched

        loader_candidates = render_loader_urls(
            unique_loaders,
            merged_sites,
            state.target_url,
        )

        attempted = state.cache.validated_attempted
        validated_ok = state.cache.validated_ok
        to_check = loader_candidates - attempted

        validated_new = await validate_urls(
            to_check,
            state.ports.http,
        )

        attempted |= to_check
        validated_ok |= validated_new
        state.add_js_urls(
            validated_new,
            source="mfe_script_loader",
            evidence_url=state.target_url,
        )

        if validated_ok:
            all_identifiers: set[str] = set()
            for ids in merged_sites.values():
                all_identifiers |= ids

            validated_prefixes: set[str] = set()
            for url in validated_ok:
                path = urlparse(url).path
                for seg in path.strip("/").split("/"):
                    if seg in all_identifiers:
                        validated_prefixes.add(seg)
            if validated_prefixes:
                subapp_urls = set()
                for scan in source_scans:
                    for literal in scan.route_literals:
                        first = literal.strip("/").split("/", 1)[0]
                        if first in validated_prefixes:
                            subapp_urls.add(literal)
                if subapp_urls:
                    state.add_route_facts(
                        subapp_urls,
                        source="mfe_active_rule",
                        evidence_url=state.target_url,
                    )
