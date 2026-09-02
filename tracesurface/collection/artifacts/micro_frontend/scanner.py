from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from tracesurface.collection.artifacts.micro_frontend.common import (
    _is_valid_app_name,
    is_strict_identifier,
)
from tracesurface.collection.artifacts.micro_frontend.harvest import (
    detect_static_script_urls,
)
from tracesurface.collection.artifacts.micro_frontend.register_apps import (
    RegisterCall,
    detect_register_calls,
)
from tracesurface.collection.artifacts.micro_frontend.script_loader import (
    LoaderTemplate,
    detect_script_injection_loaders,
)
from tracesurface.jsast import extract_string, node_text, walk_pre_iter

_MIN_SEED_OVERLAP = 2


@dataclass(slots=True)
class SourceScan:
    register_calls: list[RegisterCall]
    loaders: list[LoaderTemplate]
    static_src_urls: set[str]
    call_sites: dict[str, set[str]]
    string_arrays: list[frozenset[str]]
    route_literals: set[str]


def _collect_string_call_sites(tree_root: Node) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}

    for n in walk_pre_iter(tree_root):
        if n.type != "call_expression":
            continue
        func = n.child_by_field_name("function")
        if not func:
            continue
        call_name: str | None = None

        if func.type == "identifier":
            call_name = node_text(func)
        elif func.type == "member_expression":
            prop = func.child_by_field_name("property")
            if prop and prop.type in ("property_identifier", "identifier"):
                call_name = node_text(prop)
        if not call_name:
            continue

        args = n.child_by_field_name("arguments")
        if not (args and args.named_children):
            continue
        first = args.named_children[0]
        if first.type != "string":
            continue

        val = extract_string(first)
        if val and _is_valid_app_name(val):
            result.setdefault(call_name, set()).add(val)
    return result


def _collect_string_arrays(tree_root: Node) -> list[frozenset[str]]:
    arrays: list[frozenset[str]] = []
    for n in walk_pre_iter(tree_root):
        if n.type != "array":
            continue
        members: list[str] = []
        for child in n.named_children:
            if child.type != "string":
                members = []
                break
            val = extract_string(child)
            if val is not None:
                members.append(val)
        if members:
            arrays.append(frozenset(members))
    return arrays


def enrich_identifiers_from_cached_arrays(
    arrays: list[frozenset[str]],
    seed_identifiers: set[str],
) -> set[str]:
    if len(seed_identifiers) < _MIN_SEED_OVERLAP:
        return set()
    enriched: set[str] = set()
    for member_set in arrays:
        if len(member_set & seed_identifiers) < _MIN_SEED_OVERLAP:
            continue
        for val in member_set - seed_identifiers:
            if _is_valid_app_name(val) and is_strict_identifier(val):
                enriched.add(val)
    return enriched


def _collect_route_literals(tree_root: Node) -> set[str]:
    from tracesurface.collection import route

    literals: set[str] = set()
    for n in walk_pre_iter(tree_root):
        if n.type != "string":
            continue

        val = extract_string(n)
        if (
            val
            and val.startswith("/")
            and route.is_route_path(val)
            and not route.looks_like_asset_path(val)
        ):
            literals.add(val)
    return literals


def scan_source_tree(tree_root: Node) -> SourceScan:
    return SourceScan(
        register_calls=detect_register_calls(tree_root),
        loaders=detect_script_injection_loaders(tree_root),
        static_src_urls=detect_static_script_urls(tree_root),
        call_sites=_collect_string_call_sites(tree_root),
        string_arrays=_collect_string_arrays(tree_root),
        route_literals=_collect_route_literals(tree_root),
    )
