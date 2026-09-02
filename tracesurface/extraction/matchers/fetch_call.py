from __future__ import annotations

from tree_sitter import Node

from tracesurface.extraction.matcher_context import (
    MatcherContext,
    maybe_url,
    parse_fetch_options,
)
from tracesurface.jsast import node_text
from tracesurface.models import Param, RequestFact


def match_fetch_call(node: Node, ctx: MatcherContext) -> RequestFact | None:
    if node.type != "call_expression":
        return None

    func = node.child_by_field_name("function")
    if not func:
        return None

    if func.type == "identifier":
        if node_text(func) != "fetch":
            return None
    elif func.type == "member_expression":
        obj = func.child_by_field_name("object")
        prop = func.child_by_field_name("property")
        if not obj or not prop:
            return None
        if node_text(obj) != "window" or node_text(prop) != "fetch":
            return None
    else:
        return None

    args = node.child_by_field_name("arguments")
    if not args or not args.named_children:
        return None

    first_arg = args.named_children[0]

    if first_arg.type == "object":
        return None

    url_val = ctx.collapse_string(first_arg)
    if not maybe_url(url_val):
        return None

    method = "GET"
    params: list[Param] = []

    if len(args.named_children) > 1:
        parsed_method, parsed_params = parse_fetch_options(args.named_children[1])
        if parsed_method != "UNKNOWN":
            method = parsed_method
        params = parsed_params

    return ctx.make_match(
        node, path=url_val, method=method, params=params, url_node=first_arg
    )
