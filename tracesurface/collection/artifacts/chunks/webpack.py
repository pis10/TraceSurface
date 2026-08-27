from __future__ import annotations

import json
import re

from tree_sitter import Node

from tracesurface.collection.artifacts.chunks.types import (
    ChunkEvalPlan,
    SourceDocument,
)
from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.jsast import node_text, parse_js, walk_first_match, walk_pre_iter

WEBPACK_FINGERPRINT = "Loading chunk "


def find_string_node(node: Node, target: str) -> Node | None:
    return walk_first_match(
        node,
        lambda n: n.type == "string" and target in node_text(n),
    )


def find_ancestor_function(node: Node | None) -> Node | None:
    if node is None:
        return None
    current = node.parent
    depth = 0

    while current:
        if current.type in (
            "function_expression",
            "function_declaration",
            "arrow_function",
        ):
            depth += 1
            if depth >= 2:
                return current
        current = current.parent
    return None


def find_src_assignment(node: Node) -> Node | None:
    for n in walk_pre_iter(node):
        if n.type != "assignment_expression":
            continue
        left = n.child_by_field_name("left")

        if not left or left.type != "member_expression":
            continue
        prop = left.child_by_field_name("property")
        if prop and node_text(prop) == "src":
            return n.child_by_field_name("right")
    return None


def _find_function_def(scope_node: Node, func_name: str) -> Node | None:
    def _match_in_node(node: Node) -> Node | None:
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name and node_text(name) == func_name and value:
                if value.type in ("function_expression", "arrow_function"):
                    return value

        if node.type == "function_declaration":
            name = node.child_by_field_name("name")
            if name and node_text(name) == func_name:
                return node

        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left and node_text(left) == func_name and right:
                if right.type in ("function_expression", "arrow_function"):
                    return right
        return None

    scope = scope_node
    while scope:
        for n in walk_pre_iter(scope):
            hit = _match_in_node(n)
            if hit is not None:
                return hit
        scope = scope.parent
    return None


def find_public_path(source: str) -> str:
    for match in re.finditer(r'\.p\s*=\s*"([^"]*)"', source):
        val = match.group(1)
        if "/" in val or val == "":
            return val
    return ""


def extract_possible_params(code: str, brute_limit: int) -> list[int | str]:
    params: set[int | str] = set()
    has_string_concat = "+" in code and '".js"' in code

    for match in re.finditer(r"(?<=[{,])(\d+)\s*:", code):
        params.add(int(match.group(1)))

    for match in re.finditer(r'"([^"]{2,60})"\s*:', code):
        val = match.group(1)
        if not re.fullmatch(r"[a-f0-9]+", val):
            params.add(val)

    if has_string_concat or any(isinstance(p, int) for p in params):
        max_id = max((p for p in params if isinstance(p, int)), default=0)
        limit = max(max_id + 50, brute_limit)
        for i in range(limit):
            params.add(i)

    return list(params)


def _chunk_function(source: str, chunk_loader: Node, src_expr: Node) -> str:
    expr_code = node_text(src_expr)

    if src_expr.type == "call_expression":
        callee = src_expr.child_by_field_name("function")
        if callee and callee.type == "identifier":
            func_def = _find_function_def(chunk_loader, node_text(callee))
            if func_def:
                expr_code = node_text(func_def)
        elif callee and callee.type in ("function_expression", "arrow_function"):
            expr_code = node_text(callee)

    public_path = find_public_path(source)
    expr_code = re.sub(r"\b\w\.p\b", json.dumps(public_path), expr_code)

    if expr_code.lstrip().startswith("function"):
        return expr_code

    params_node = chunk_loader.child_by_field_name("parameters")
    param_name = "e"
    if params_node and params_node.named_children:
        param_name = node_text(params_node.named_children[0])
    return f"function({param_name}) {{ return {expr_code}; }}"


def build_webpack_eval_plan(source: SourceDocument) -> ChunkEvalPlan | None:
    if WEBPACK_FINGERPRINT not in source.text:
        return None

    tree_root = source.tree_root
    if tree_root is None:
        tree_root = parse_js(source.text).root_node

    loading_node = find_string_node(tree_root, WEBPACK_FINGERPRINT)
    if not loading_node:
        return None

    chunk_loader = find_ancestor_function(loading_node)
    if not chunk_loader:
        return None

    src_expr = find_src_assignment(chunk_loader)
    if not src_expr:
        return None

    chunk_fn = _chunk_function(source.text, chunk_loader, src_expr)
    params = extract_possible_params(
        chunk_fn,
        DEFAULT_SETTINGS.collection.chunk_brute_force_max,
    )
    if not params:
        params = list(range(DEFAULT_SETTINGS.collection.chunk_brute_force_max))
    return ChunkEvalPlan(chunk_fn, tuple(params))
