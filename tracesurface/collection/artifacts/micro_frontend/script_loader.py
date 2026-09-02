from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from tree_sitter import Node

from tracesurface.collection.artifacts.micro_frontend.common import (
    _find_enclosing_function,
    _get_function_params,
    _is_src_assignment,
)
from tracesurface.collection.deps import HttpClientTimeoutError, HttpTextClient
from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.jsast import extract_string, node_text, walk_pre_iter


async def validate_urls(
    candidates: set[str],
    http_client: HttpTextClient,
) -> set[str]:
    if not candidates:
        return set()

    validated: set[str] = set()

    async def _check(url: str):
        try:
            async with http_client.stream(
                "GET",
                url,
                timeout=DEFAULT_SETTINGS.collection.mfe_validate_timeout_s,
                follow_redirects=True,
            ) as resp:
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    if "html" not in ct:
                        validated.add(url)

        except HttpClientTimeoutError:
            pass
        except Exception:
            pass

    await http_client.map(candidates, _check)
    return validated


_PARAM_PLACEHOLDER_PATTERN = re.compile(r"\{P:([A-Za-z_$][\w$]*)\}")

_MIN_TEMPLATE_LEN = 6


@dataclass(frozen=True, slots=True)
class LoaderTemplate:
    func_name: str
    template: str


_TEMPLATE_CONCAT_MAX_DEPTH = 32


def _extract_template_from_concat_expr(
    expr: Node,
    param_set: frozenset[str],
) -> str | None:
    def walk(n: Node, _depth: int = 0) -> str | None:
        if _depth > _TEMPLATE_CONCAT_MAX_DEPTH:
            return None

        if n.type == "string":
            return extract_string(n) or ""

        if n.type == "identifier":
            name = node_text(n)
            if name in param_set:
                return "{P:" + name + "}"
            return None

        if n.type == "template_string":
            parts: list[str] = []
            for c in n.named_children:
                if c.type == "string_fragment":
                    parts.append(node_text(c))
                elif c.type == "template_substitution":
                    inner_children = [x for x in c.named_children]
                    if len(inner_children) == 1:
                        piece = walk(inner_children[0], _depth + 1)
                        if piece is None:
                            return None
                        parts.append(piece)
                    else:
                        return None
                else:
                    return None
            return "".join(parts)

        if n.type == "binary_expression":
            op = n.child_by_field_name("operator")
            if not op or node_text(op) != "+":
                return None
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")
            if not left or not right:
                return None
            a = walk(left, _depth + 1)
            b = walk(right, _depth + 1)
            if a is None or b is None:
                return None
            return a + b

        if n.type == "parenthesized_expression":
            inner = next(iter(n.named_children), None)
            return walk(inner, _depth + 1) if inner is not None else None

        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if not func or func.type != "member_expression":
                return None
            prop = func.child_by_field_name("property")
            if not prop or node_text(prop) != "concat":
                return None
            receiver = func.child_by_field_name("object")
            if not receiver:
                return None
            base = walk(receiver, _depth + 1)
            if base is None:
                return None
            args = n.child_by_field_name("arguments")
            if not args:
                return None
            pieces = [base]
            for a in args.named_children:
                piece = walk(a, _depth + 1)

                if piece is None:
                    return None
                pieces.append(piece)
            return "".join(pieces)

        return None

    return walk(expr)


def _get_function_name(func_node: Node) -> str | None:
    name_node = func_node.child_by_field_name("name")
    if name_node and name_node.type == "identifier":
        return node_text(name_node)

    parent = func_node.parent

    if parent and parent.type == "variable_declarator":
        name_node = parent.child_by_field_name("name")
        if name_node and name_node.type == "identifier":
            return node_text(name_node)

    if parent and parent.type == "pair":
        sibling_key_node = parent.child_by_field_name("key")
        if sibling_key_node and node_text(sibling_key_node) == "value":
            obj = parent.parent
            if obj and obj.type == "object":
                for child in obj.named_children:
                    if child.type != "pair":
                        continue
                    k = child.child_by_field_name("key")
                    v = child.child_by_field_name("value")
                    if k and v and node_text(k) == "key" and v.type == "string":
                        return extract_string(v)

    if parent and parent.type == "pair":
        key_node = parent.child_by_field_name("key")
        if key_node:
            name = (
                extract_string(key_node)
                if key_node.type == "string"
                else node_text(key_node)
            )
            return name

    if func_node.type == "method_definition":
        key_node = func_node.child_by_field_name("name")
        if key_node:
            name = (
                extract_string(key_node)
                if key_node.type == "string"
                else node_text(key_node)
            )
            return name

    if parent and parent.type == "assignment_expression":
        left = parent.child_by_field_name("left")
        if left:
            if left.type == "identifier":
                return node_text(left)
            if left.type == "member_expression":
                prop = left.child_by_field_name("property")
                if prop and prop.type in ("property_identifier", "identifier"):
                    return node_text(prop)

    return None


def detect_script_injection_loaders(
    tree_root: Node,
) -> list[LoaderTemplate]:
    found: dict[tuple[str, str], LoaderTemplate] = {}

    for n in walk_pre_iter(tree_root):
        if not _is_src_assignment(n):
            continue

        func_node = _find_enclosing_function(n)
        if not func_node:
            continue
        params = _get_function_params(func_node)
        if not params:
            continue
        param_set = frozenset(params)
        right = n.child_by_field_name("right")
        if not right:
            continue

        template = _extract_template_from_concat_expr(right, param_set)
        if not (template and len(template) >= _MIN_TEMPLATE_LEN):
            continue

        if not _PARAM_PLACEHOLDER_PATTERN.search(template):
            continue
        name = _get_function_name(func_node)
        if not name:
            continue
        key = (name, template)
        if key not in found:
            found[key] = LoaderTemplate(
                func_name=name,
                template=template,
            )
    return list(found.values())


def render_loader_urls(
    loaders: list[LoaderTemplate],
    call_sites: dict[str, set[str]],
    base_url: str,
) -> set[str]:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    out: set[str] = set()
    for loader in loaders:
        identifiers = call_sites.get(loader.func_name, set())
        if not identifiers:
            continue
        for ident in identifiers:
            url_path = _PARAM_PLACEHOLDER_PATTERN.sub(
                lambda _match, v=ident: v,
                loader.template,
            )

            if url_path.startswith("http://") or url_path.startswith("https://"):
                out.add(url_path.split("?")[0])
            elif url_path.startswith("/"):
                out.add(origin + url_path.split("?")[0])
    return out
