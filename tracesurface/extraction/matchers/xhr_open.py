from __future__ import annotations

from tree_sitter import Node

from tracesurface.extraction.matcher_context import (
    HTTP_METHODS,
    MatcherContext,
    extract_params,
    maybe_url,
    unwrap_json_stringify,
)
from tracesurface.jsast import extract_string, node_text, walk_pre_iter
from tracesurface.models import Param, RequestFact


def match_xhr_open(node: Node, ctx: MatcherContext) -> RequestFact | None:
    if node.type != "call_expression":
        return None

    func = node.child_by_field_name("function")
    if not func or func.type != "member_expression":
        return None

    prop = func.child_by_field_name("property")
    if not prop or node_text(prop) != "send":
        return None

    obj = func.child_by_field_name("object")
    if not obj:
        return None
    obj_name = node_text(obj)

    send_stmt = node.parent
    if not send_stmt:
        return None

    open_call = _find_open_call(send_stmt, obj_name)
    if not open_call:
        return None

    open_args = open_call.child_by_field_name("arguments")
    if not open_args or len(open_args.named_children) < 2:
        return None

    method_node = open_args.named_children[0]
    url_node = open_args.named_children[1]

    method_str = extract_string(method_node)
    if not method_str or method_str.lower() not in HTTP_METHODS:
        return None

    url_val = ctx.collapse_string(url_node)
    if not maybe_url(url_val):
        return None

    send_args = node.child_by_field_name("arguments")
    params = _extract_send_params(send_args)

    return ctx.make_match(
        node, path=url_val, method=method_str, params=params, url_node=url_node
    )


def _find_open_call(send_stmt: Node, obj_name: str) -> Node | None:
    block = send_stmt.parent
    if not block:
        return None

    siblings = block.named_children
    stmt_idx = None
    for i, s in enumerate(siblings):
        if s.id == send_stmt.id:
            stmt_idx = i
            break
    if stmt_idx is None:
        return None

    for i in range(stmt_idx - 1, max(stmt_idx - 11, -1), -1):
        result = _search_open(siblings[i], obj_name)
        if result:
            return result

    return None


def _search_open(node: Node, obj_name: str) -> Node | None:
    for n in walk_pre_iter(node):
        if n.type != "call_expression":
            continue
        func = n.child_by_field_name("function")
        if not func or func.type != "member_expression":
            continue
        prop = func.child_by_field_name("property")
        obj = func.child_by_field_name("object")
        if prop and obj and node_text(prop) == "open" and node_text(obj) == obj_name:
            return n
    return None


def _extract_send_params(args: Node | None) -> list[Param]:
    if not args or not args.named_children:
        return []
    return extract_params(unwrap_json_stringify(args.named_children[0]), "body")
