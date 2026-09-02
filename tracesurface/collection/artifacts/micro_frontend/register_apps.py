from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from tracesurface.collection.artifacts.micro_frontend.common import _FUNCTION_NODE_TYPES
from tracesurface.jsast import (
    extract_literal_value,
    extract_string,
    get_object_props,
    node_text,
    walk_pre_iter,
)

AppConfig = dict[str, object]

REGISTER_API_NAMES = frozenset(
    {
        "registerMicroApps",
        "registerApplication",
        "loadMicroApp",
    }
)


@dataclass(slots=True)
class RegisterCall:
    api_name: str
    apps: list[AppConfig]


def _find_function_scope(node: Node) -> Node | None:
    cur = node.parent
    while cur:
        if cur.type in _FUNCTION_NODE_TYPES or cur.type == "program":
            return cur
        cur = cur.parent
    return None


_NESTED_STATEMENT_TYPES = frozenset(
    {
        "statement_block",
        "if_statement",
        "for_statement",
        "while_statement",
    }
)


def _find_binding_in_scope(scope: Node, name: str) -> Node | None:
    body_node = scope.child_by_field_name("body") or scope
    stack: list[Node] = [body_node]
    while stack:
        container = stack.pop()
        for child in container.named_children:
            t = child.type

            if t in _FUNCTION_NODE_TYPES:
                continue

            if t == "lexical_declaration" or t == "variable_declaration":
                for d in child.named_children:
                    if d.type != "variable_declarator":
                        continue
                    name_node = d.child_by_field_name("name")
                    value_node = d.child_by_field_name("value")
                    if name_node and value_node and node_text(name_node) == name:
                        return value_node

            if t in _NESTED_STATEMENT_TYPES:
                stack.append(child)
    return None


def _extract_apps_from_array(array_node: Node) -> list[AppConfig]:
    if array_node.type != "array":
        return []
    out: list[AppConfig] = []

    for child in array_node.named_children:
        if child.type == "object":
            cfg = _extract_single_app_config(child)
            if cfg:
                out.append(cfg)
    return out


def _extract_single_app_config(obj_node: Node) -> AppConfig | None:
    if obj_node.type != "object":
        return None
    props = get_object_props(obj_node)

    name_node = props.get("name")
    entry_node = props.get("entry")
    if not name_node or not entry_node:
        return None

    name = extract_string(name_node)
    if not name:
        return None

    entry = extract_literal_value(entry_node)
    if entry == "?":
        return None

    active_rule_node = props.get("activeRule")
    active_rule = None
    if active_rule_node:
        if active_rule_node.type == "string":
            active_rule = extract_string(active_rule_node)
        elif active_rule_node.type == "array":
            vals = []
            for c in active_rule_node.named_children:
                if c.type == "string":
                    v = extract_string(c)
                    if v:
                        vals.append(v)
            if vals:
                active_rule = vals[0] if len(vals) == 1 else vals

    return {"name": name, "entry": entry, "activeRule": active_rule}


def _extract_apps_from_map_callback(call_expr: Node) -> list[AppConfig]:
    if call_expr.type != "call_expression":
        return []

    func = call_expr.child_by_field_name("function")
    if not func or func.type != "member_expression":
        return []
    prop = func.child_by_field_name("property")
    if not prop or node_text(prop) != "map":
        return []
    args = call_expr.child_by_field_name("arguments")
    if not args or not args.named_children:
        return []

    callback = args.named_children[0]
    if callback.type == "arrow_function":
        body = callback.child_by_field_name("body")
        if body:
            if body.type == "parenthesized_expression":
                for c in body.named_children:
                    cfg = _extract_single_app_config(c)
                    if cfg:
                        return [cfg]

            if body.type == "object":
                cfg = _extract_single_app_config(body)
                if cfg:
                    return [cfg]

            if body.type == "statement_block":
                for stmt in body.named_children:
                    if stmt.type == "return_statement":
                        for c in stmt.named_children:
                            if c.type == "parenthesized_expression":
                                for inner in c.named_children:
                                    cfg = _extract_single_app_config(inner)
                                    if cfg:
                                        return [cfg]
                            elif c.type == "object":
                                cfg = _extract_single_app_config(c)
                                if cfg:
                                    return [cfg]

    elif callback.type in ("function_expression", "function_declaration"):
        body = callback.child_by_field_name("body")
        if body:
            for stmt in body.named_children:
                if stmt.type == "return_statement":
                    for c in stmt.named_children:
                        if c.type == "object":
                            cfg = _extract_single_app_config(c)
                            if cfg:
                                return [cfg]
    return []


_RESOLVE_REGISTER_MAX_DEPTH = 16


def _resolve_register_arg(
    arg: Node,
    call_node: Node,
    _visited: set[int] | None = None,
    _depth: int = 0,
) -> list[AppConfig]:
    if _visited is None:
        _visited = set()
    if _depth > _RESOLVE_REGISTER_MAX_DEPTH:
        return []

    node_id = id(arg)
    if node_id in _visited:
        return []
    _visited.add(node_id)

    if arg.type == "array":
        return _extract_apps_from_array(arg)
    if arg.type == "object":
        cfg = _extract_single_app_config(arg)
        return [cfg] if cfg else []

    if arg.type == "identifier":
        scope = _find_function_scope(call_node)
        if scope:
            binding = _find_binding_in_scope(scope, node_text(arg))
            if binding:
                return _resolve_register_arg(binding, call_node, _visited, _depth + 1)
        return []

    if arg.type == "call_expression":
        func = arg.child_by_field_name("function")
        is_map_call = (
            func is not None
            and func.type == "member_expression"
            and (p := func.child_by_field_name("property")) is not None
            and node_text(p) == "map"
        )
        if is_map_call:
            return _extract_apps_from_map_callback(arg)

        return []

    if arg.type in ("await_expression", "parenthesized_expression"):
        inner = next(iter(arg.named_children), None)
        if inner is not None:
            return _resolve_register_arg(inner, call_node, _visited, _depth + 1)

    return []


def detect_register_calls(tree_root: Node) -> list[RegisterCall]:
    results: list[RegisterCall] = []

    for n in walk_pre_iter(tree_root):
        if n.type != "call_expression":
            continue
        func = n.child_by_field_name("function")
        if not func:
            continue
        api_name: str | None = None

        if func.type == "identifier":
            name = node_text(func)
            if name in REGISTER_API_NAMES:
                api_name = name

        elif func.type == "member_expression":
            prop = func.child_by_field_name("property")
            if prop:
                name = node_text(prop)
                if name in REGISTER_API_NAMES:
                    api_name = name
        if not api_name:
            continue

        args = n.child_by_field_name("arguments")
        if not (args and args.named_children):
            continue
        first = args.named_children[0]
        apps = _resolve_register_arg(first, n)
        results.append(
            RegisterCall(
                api_name=api_name,
                apps=apps,
            )
        )
    return results
