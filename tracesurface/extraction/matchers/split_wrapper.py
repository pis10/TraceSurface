from __future__ import annotations

from tree_sitter import Node

from tracesurface.extraction.matcher_context import (
    EXPR,
    HTTP_METHODS,
    MatcherContext,
    maybe_url,
    parse_fetch_options,
)
from tracesurface.jsast import node_text
from tracesurface.models import Param, RequestFact

_VERB_METHODS = (
    ("post", "POST"),
    ("put", "PUT"),
    ("patch", "PATCH"),
    ("delete", "DELETE"),
    ("del", "DELETE"),
    ("get", "GET"),
)


def match_split_wrapper(node: Node, ctx: MatcherContext) -> RequestFact | None:
    if not ctx.wrapper_prefixes:
        return None
    if node.type != "call_expression":
        return None

    func = node.child_by_field_name("function")
    if not func or func.type != "member_expression":
        return None
    prop = func.child_by_field_name("property")
    if not prop:
        return None
    method = _verb_method(node_text(prop).lstrip("$"))
    if method is None:
        return None

    args = node.child_by_field_name("arguments")
    if not args or len(args.named_children) < 2:
        return None

    children = args.named_children
    start = 1 if children[0].type == "object" else 0
    if start + 1 >= len(children):
        return None

    gw_val = _path_seg(ctx.collapse_string(children[start]))
    path_val = _path_seg(ctx.collapse_string(children[start + 1]))
    if not gw_val or not path_val:
        return None

    infix = ctx.wrapper_prefixes.get(gw_val)
    if not infix:
        return None

    full_path = f"/{gw_val}/{infix}/{path_val}"
    if not maybe_url(full_path):
        return None

    params: list[Param] = []
    if start + 2 < len(children):
        _, params = parse_fetch_options(children[start + 2])

    return ctx.make_match(node, path=full_path, method=method, params=params)


def _verb_method(prop_name: str) -> str | None:
    low = prop_name.lower()

    if low in HTTP_METHODS:
        return low.upper()

    for token, method in _VERB_METHODS:
        if token in low:
            return method
    return None


def _path_seg(s: str) -> str | None:
    if not s or s == EXPR or EXPR in s:
        return None

    if s.startswith("http") or "//" in s or " " in s:
        return None
    seg = s.strip("/")

    if not seg or any(c in seg for c in "?#&=:"):
        return None
    return seg.lower()
