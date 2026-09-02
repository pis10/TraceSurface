from __future__ import annotations

from tree_sitter import Node

from tracesurface.extraction.matcher_context import (
    HTTP_METHODS,
    MatcherContext,
    maybe_url,
    parse_axios_args,
)
from tracesurface.jsast import node_text
from tracesurface.models import Param, RequestFact


def match_member_method(node: Node, ctx: MatcherContext) -> RequestFact | None:
    if node.type != "call_expression":
        return None

    func = node.child_by_field_name("function")
    if not func or func.type != "member_expression":
        return None

    prop = func.child_by_field_name("property")
    if not prop:
        return None

    method_name = node_text(prop).lower().lstrip("$")
    if method_name not in HTTP_METHODS:
        return None

    args = node.child_by_field_name("arguments")
    if not args or not args.named_children:
        return None

    first_arg = args.named_children[0]
    url_val = ctx.collapse_string(first_arg)
    if not maybe_url(url_val):
        return None

    params: list[Param] = []
    if len(args.named_children) > 1:
        params = parse_axios_args(args.named_children[1], method_name)

    return ctx.make_match(
        node, path=url_val, method=method_name, params=params, url_node=first_arg
    )
