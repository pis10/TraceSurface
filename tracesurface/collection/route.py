from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from tree_sitter import Node

from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.jsast import (
    extract_string,
    get_object_props,
    node_text,
    walk_pre_iter,
)

_STRICT_COMPANIONS = {"component", "components", "element", "redirect"}


def _is_dynamic(path: str) -> bool:
    return ":" in path or "*" in path


_DYNAMIC_PARAM = re.compile(r":[A-Za-z_][\w]*(?:\((?:[^()]|\([^()]*\))*\))?\??")


def is_route_path(s: str) -> bool:
    if not s or not s.startswith("/"):
        return False
    if s.strip("/") == "":
        return False
    if "*" in s:
        return False
    return True


def fill_dynamic_params(route: str) -> str:
    return _DYNAMIC_PARAM.sub(
        DEFAULT_SETTINGS.route_materialization.path_fill,
        route,
    )


def _normalize(path: str) -> str:
    path = re.sub(r"/{2,}", "/", path)

    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def join_route(parent: str, raw: str) -> str:
    if raw.startswith("/"):
        return _normalize(raw)
    base = parent or "/"

    if raw == "":
        return _normalize(base)

    return _normalize(f"{base.rstrip('/')}/{raw.lstrip('/')}")


def _iter_array_objects(node: Node):
    if node.type != "array":
        return
    for child in node.named_children:
        if child.type == "object":
            yield child


def _visit_routes(
    node: Node,
    router_routes: set[str],
    named_routes: set[str] | None = None,
    parent_path: str = "",
):
    stack: list[tuple[Node, str]] = [(node, parent_path)]
    while stack:
        cur, parent = stack.pop()

        handled_as_route = False
        if cur.type == "object":
            props = get_object_props(cur)

            if "path" in props:
                raw_path = extract_string(props["path"])
                if raw_path is not None:
                    has_router_companion = any(k in props for k in _STRICT_COMPANIONS)

                    has_named_navigation = (
                        named_routes is not None
                        and "name" in props
                        and not _is_dynamic(raw_path)
                    )
                    if has_router_companion or has_named_navigation:
                        full = join_route(parent, raw_path)
                        if full and is_route_path(full):
                            if has_router_companion:
                                router_routes.add(full)
                            elif named_routes is not None:
                                named_routes.add(full)

                        if "children" in props:
                            children_node = props["children"]
                            if children_node and children_node.type == "array":
                                next_parent = full if full else parent
                                child_objs = list(_iter_array_objects(children_node))

                                for obj in reversed(child_objs):
                                    stack.append((obj, next_parent))
                                handled_as_route = True

        if not handled_as_route:
            for child in reversed(list(cur.children)):
                stack.append((child, parent))


_ASSET_EXTENSIONS = (
    ".js",
    ".mjs",
    ".css",
    ".html",
    ".htm",
    ".json",
    ".xml",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".map",
    ".wasm",
    ".mp4",
    ".mp3",
    ".pdf",
    ".zip",
)


def looks_like_asset_path(path: str) -> bool:
    segments = path.rstrip("/").split("/")
    last = segments[-1].lower()
    return any(last.endswith(ext) for ext in _ASSET_EXTENSIONS)


def _member_chain_endswith(node: Node, prop_name: str) -> bool:
    if node.type != "member_expression":
        return False
    prop = node.child_by_field_name("property")
    if not prop or prop.type not in ("property_identifier", "identifier"):
        return False
    return node_text(prop) == prop_name


def _member_chain_includes(node: Node, target_names: frozenset[str]) -> bool:
    cur = node
    while cur is not None:
        if cur.type == "identifier":
            if node_text(cur) in target_names:
                return True
            return False
        if cur.type == "member_expression":
            prop = cur.child_by_field_name("property")
            if prop and node_text(prop) in target_names:
                return True
            cur = cur.child_by_field_name("object")
            continue

        return False
    return False


_LOCATION_NAMES = frozenset({"location"})
_HISTORY_NAMES = frozenset({"history"})


def _visit_w3c_nav(node: Node, out: set[str]):
    for n in walk_pre_iter(node):
        if n.type == "assignment_expression":
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")

            if (
                left
                and left.type == "member_expression"
                and right
                and right.type == "string"
                and _member_chain_endswith(left, "href")
            ):
                obj = left.child_by_field_name("object")

                if obj and _member_chain_includes(obj, _LOCATION_NAMES):
                    val = extract_string(right)

                    if val and val.startswith("/") and is_route_path(val):
                        out.add(val)

        elif n.type == "call_expression":
            func = n.child_by_field_name("function")
            args = n.child_by_field_name("arguments")
            if func and func.type == "member_expression" and args:
                prop = func.child_by_field_name("property")
                obj = func.child_by_field_name("object")
                if prop and obj:
                    method = node_text(prop)

                    arg_idx = None
                    if method in ("assign", "replace") and _member_chain_includes(
                        obj, _LOCATION_NAMES
                    ):
                        arg_idx = 0
                    elif method in (
                        "pushState",
                        "replaceState",
                    ) and _member_chain_includes(obj, _HISTORY_NAMES):
                        arg_idx = 2

                    if arg_idx is not None and len(args.named_children) > arg_idx:
                        arg = args.named_children[arg_idx]
                        if arg.type == "string":
                            val = extract_string(arg)
                            if val and val.startswith("/") and is_route_path(val):
                                out.add(val)


def extract_route_sets_from_tree(
    tree_root: Node,
) -> tuple[set[str], set[str], set[str]]:
    router_routes: set[str] = set()
    named_routes: set[str] = set()
    w3c_routes: set[str] = set()
    _visit_routes(tree_root, router_routes, named_routes)
    _visit_w3c_nav(tree_root, w3c_routes)
    return router_routes, named_routes, w3c_routes
def build_route_url(target_url: str, route: str, hash_prefix: str) -> str:
    parsed = urlparse(target_url)

    if hash_prefix:
        fragment = hash_prefix.lstrip("#") + route.lstrip("/")
        return urlunparse(parsed._replace(fragment=fragment))

    return urlunparse(parsed._replace(path=route, fragment=""))
