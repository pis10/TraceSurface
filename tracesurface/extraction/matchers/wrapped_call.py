from __future__ import annotations

from tree_sitter import Node

from tracesurface.extraction.matcher_context import (
    HTTP_METHODS,
    WRAPPED_CALL_SKIP_RULES,
    MatcherContext,
    get_callee_name,
    maybe_url,
    parse_fetch_options,
)
from tracesurface.jsast import node_text
from tracesurface.models import Param, RequestFact


def match_wrapped_call(node: Node, ctx: MatcherContext) -> RequestFact | None:
    if node.type != "call_expression":
        return None

    func = node.child_by_field_name("function")
    if not func:
        return None

    if _is_member_with_http_method(func):
        return None

    if func.type == "identifier" and node_text(func) == "fetch":
        return None
    if func.type == "member_expression":
        obj = func.child_by_field_name("object")
        prop = func.child_by_field_name("property")
        if obj and prop and node_text(obj) == "window" and node_text(prop) == "fetch":
            return None

    args = node.child_by_field_name("arguments")
    if not args or not args.named_children:
        return None

    first_arg = args.named_children[0]

    if first_arg.type == "object":
        return None

    first_val = ctx.collapse_string(first_arg)
    if (
        first_arg.type == "string"
        and first_val.lower() in HTTP_METHODS
        and len(args.named_children) >= 2
    ):
        second_arg = args.named_children[1]
        url_val = ctx.collapse_string(second_arg)
        if maybe_url(url_val):
            callee = get_callee_name(func)
            if callee in WRAPPED_CALL_SKIP_RULES.funcs:
                return None
            if callee and callee.startswith(WRAPPED_CALL_SKIP_RULES.prefixes):
                return None

            method_params: list[Param] = []
            if len(args.named_children) > 2:
                _, method_params = parse_fetch_options(args.named_children[2])

            return ctx.make_match(
                node,
                path=url_val,
                method=first_val,
                params=method_params,
                url_node=second_arg,
            )

    url_val = first_val
    if not maybe_url(url_val):
        return None

    callee = get_callee_name(func)
    if callee in WRAPPED_CALL_SKIP_RULES.funcs:
        return None
    if callee and callee.startswith(WRAPPED_CALL_SKIP_RULES.prefixes):
        return None

    if url_val.startswith("./") or url_val.startswith("../"):
        return None
    if "#/" in url_val:
        return None

    method = "UNKNOWN"
    params: list[Param] = []
    if len(args.named_children) > 1:
        method, params = parse_fetch_options(args.named_children[1])

    return ctx.make_match(
        node, path=url_val, method=method, params=params, url_node=first_arg
    )


def _is_member_with_http_method(func_node: Node) -> bool:
    if not func_node or func_node.type != "member_expression":
        return False
    prop = func_node.child_by_field_name("property")
    if not prop:
        return False
    name = node_text(prop).lower().lstrip("$")
    return name in HTTP_METHODS
