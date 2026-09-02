from __future__ import annotations

from tree_sitter import Node

from tracesurface.extraction.matcher_context import (
    HTTP_METHODS,
    OBJECT_CONFIG_SKIP_RULES,
    REQUEST_OPTION_SLOTS,
    MatcherContext,
    get_callee_name,
    maybe_url,
    parse_fetch_options,
)
from tracesurface.jsast import get_object_props, node_text
from tracesurface.models import RequestFact
from tracesurface.urls import combine_urls


def match_object_config(node: Node, ctx: MatcherContext) -> RequestFact | None:
    if node.type != "call_expression":
        return None

    func = node.child_by_field_name("function")
    if func:
        method_name = get_callee_name(func) if func.type == "member_expression" else ""
        if method_name in OBJECT_CONFIG_SKIP_RULES.funcs:
            return None
        if method_name and method_name.startswith(OBJECT_CONFIG_SKIP_RULES.prefixes):
            return None

    args = node.child_by_field_name("arguments")
    if not args:
        return None

    for arg in args.named_children:
        if arg.type != "object":
            continue

        props = get_object_props(arg)
        if "url" not in props:
            continue

        url_val = ctx.collapse_string(props["url"])
        if not maybe_url(url_val):
            continue

        base_val = _inline_base(props, ctx)
        if base_val:
            url_val = combine_urls(base_val, url_val)

        method, params = parse_fetch_options(arg)

        if method == "UNKNOWN" and func and func.type == "member_expression":
            prop = func.child_by_field_name("property")
            if prop:
                prop_name = node_text(prop).lower().lstrip("$")
                if prop_name in HTTP_METHODS:
                    method = prop_name

        return ctx.make_match(
            node,
            path=url_val,
            method=method,
            params=params,
            url_node=props["url"],
        )

    return None


def _inline_base(props: dict[str, Node], ctx: MatcherContext) -> str | None:
    for key in REQUEST_OPTION_SLOTS.base_keys:
        if key in props:
            val = ctx.collapse_string(props[key])

            if val and "EXPR" not in val:
                return val
    return None
