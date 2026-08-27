from __future__ import annotations

import json
from urllib.parse import urljoin, urlparse

from tree_sitter import Node

from tracesurface.collection.artifacts.micro_frontend.common import (
    _find_enclosing_function,
    _get_function_params,
    _is_src_assignment,
    _is_valid_app_name,
    is_strict_identifier,
)
from tracesurface.collection.deps import CpuPort, HttpClientTimeoutError, HttpTextClient
from tracesurface.config import DEFAULT_SETTINGS
from tracesurface.jsast import (
    extract_literal_value,
    extract_string,
    node_text,
    parse_js,
    walk_first_match,
    walk_pre_iter,
)


def _extract_literal_url_prefix(
    expr: Node,
    formal_param_names: frozenset[str],
) -> str | None:
    def refs_any_formal_param(root: Node) -> bool:
        for sub in walk_pre_iter(root):
            if sub.type == "identifier" and node_text(sub) in formal_param_names:
                return True
        return False

    if formal_param_names and refs_any_formal_param(expr):
        return None

    if expr.type == "string":
        return extract_string(expr)

    if expr.type == "template_string":
        for c in expr.named_children:
            if c.type == "string_fragment":
                return node_text(c)
            if c.type == "template_substitution":
                return None
        return None

    if expr.type == "binary_expression":
        op = expr.child_by_field_name("operator")
        if not op or node_text(op) != "+":
            return None
        left = expr.child_by_field_name("left")
        if left and left.type == "string":
            return extract_string(left)
        return None

    if expr.type == "call_expression":
        func = expr.child_by_field_name("function")
        if func and func.type == "member_expression":
            prop = func.child_by_field_name("property")
            if prop and node_text(prop) == "concat":
                obj = func.child_by_field_name("object")
                if obj and obj.type == "string":
                    return extract_string(obj)
        return None
    return None


def detect_static_script_urls(tree_root: Node) -> set[str]:
    urls: set[str] = set()

    for n in walk_pre_iter(tree_root):
        if not _is_src_assignment(n):
            continue
        right = n.child_by_field_name("right")
        if not right:
            continue

        func_node = _find_enclosing_function(n)
        params = (
            frozenset(_get_function_params(func_node)) if func_node else frozenset()
        )
        prefix = _extract_literal_url_prefix(right, params)

        if prefix and (
            prefix.startswith("/") or prefix.startswith(("http://", "https://"))
        ):
            clean = prefix.rstrip("?").rstrip()
            if clean:
                urls.add(clean)
    return urls


_HARVEST_MAX_DEPTH = 4


def parse_config_body(text: str) -> dict[str, object] | None:
    t = text.strip()
    if not t:
        return None

    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        tree = parse_js(t)
    except Exception:
        return None

    def _is_top_level_object_literal(n: Node) -> bool:
        if n.type == "assignment_expression":
            right = n.child_by_field_name("right")
            return bool(right and right.type == "object")
        if n.type == "variable_declarator":
            val = n.child_by_field_name("value")
            return bool(val and val.type == "object")
        return False

    holder = walk_first_match(tree.root_node, _is_top_level_object_literal)
    obj_node: Node | None = None
    if holder is not None:
        if holder.type == "assignment_expression":
            obj_node = holder.child_by_field_name("right")
        elif holder.type == "variable_declarator":
            obj_node = holder.child_by_field_name("value")
    if not obj_node:
        return None
    value = extract_literal_value(obj_node)
    return value if isinstance(value, dict) else None


def _harvest_string(s: str, depth: int) -> set[str]:
    stripped = s.strip()
    if not stripped:
        return set()

    if depth < _HARVEST_MAX_DEPTH and stripped[0] in "{[":
        try:
            inner = json.loads(stripped)
            return _harvest_recursive(inner, 0)
        except (json.JSONDecodeError, ValueError):
            pass

    if "," in stripped:
        out: set[str] = set()
        for part in stripped.split(","):
            t = part.strip()
            if is_strict_identifier(t) and _is_valid_app_name(t):
                out.add(t)
        return out

    if is_strict_identifier(stripped) and _is_valid_app_name(stripped):
        return {stripped}
    return set()


def _harvest_recursive(node, depth: int) -> set[str]:
    out: set[str] = set()

    stack: list[tuple[object, int]] = [(node, depth)]
    while stack:
        cur, d = stack.pop()

        if d > _HARVEST_MAX_DEPTH:
            continue

        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(k, str):
                    out |= _harvest_string(k, d)
                stack.append((v, d + 1))

        elif isinstance(cur, list):
            for item in cur:
                stack.append((item, d + 1))

        elif isinstance(cur, str):
            out |= _harvest_string(cur, d)
    return out


def harvest_identifiers_from_body(body: str) -> set[str]:
    if not body:
        return set()
    data = parse_config_body(body)
    if data is None:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return set()
    return _harvest_recursive(data, 0)


def _absolutize_url(u: str, target_url: str) -> str:
    if u.startswith(("http://", "https://")):
        return u.split("?")[0]
    if u.startswith("/"):
        parsed = urlparse(target_url)
        return f"{parsed.scheme}://{parsed.netloc}{u}".split("?")[0]
    return urljoin(target_url, u).split("?")[0]


async def harvest_identifiers(
    ast_static_urls: set[str],
    cdp_response_bodies: dict[str, str],
    target_url: str,
    http_client: HttpTextClient,
    cpu: CpuPort,
    cache: dict[str, set[str]] | None = None,
) -> set[str]:
    identifiers: set[str] = set()

    for url, body in cdp_response_bodies.items():
        if cache is not None:
            ckey = f"cdp:{url}"
            cached = cache.get(ckey)
            if cached is not None:
                identifiers |= cached
                continue
        ids = await cpu.run(harvest_identifiers_from_body, body)
        if len(ids) > DEFAULT_SETTINGS.collection.mfe_harvest_max_ids_per_body:
            ids = set(
                sorted(ids)[: DEFAULT_SETTINGS.collection.mfe_harvest_max_ids_per_body]
            )
        if cache is not None:
            cache[f"cdp:{url}"] = ids
        identifiers |= ids

    cdp_urls = set(cdp_response_bodies.keys())
    urls_to_fetch: set[str] = set()
    for u in ast_static_urls:
        abs_u = _absolutize_url(u, target_url)
        if abs_u not in cdp_urls:
            urls_to_fetch.add(u)

    if urls_to_fetch:
        async def _fetch_and_harvest(url: str) -> set[str]:
            if cache is not None:
                ckey = f"http:{_absolutize_url(url, target_url)}"
                cached = cache.get(ckey)
                if cached is not None:
                    return cached

            abs_url = (
                url
                if url.startswith(("http://", "https://"))
                else urljoin(target_url, url)
            )

            try:
                resp = await http_client.get(
                    abs_url,
                    timeout=DEFAULT_SETTINGS.collection.mfe_validate_timeout_s,
                    follow_redirects=True,
                )

                if resp.status_code != 200:
                    result: set[str] = set()
                elif (
                    len(resp.content)
                    > DEFAULT_SETTINGS.collection.response_body_capture_limit
                ):
                    result = set()
                else:
                    text = await http_client.text(resp)
                    result = await cpu.run(harvest_identifiers_from_body, text)
                    if (
                        len(result)
                        > DEFAULT_SETTINGS.collection.mfe_harvest_max_ids_per_body
                    ):
                        result = set(
                            sorted(result)[
                                : DEFAULT_SETTINGS.collection.mfe_harvest_max_ids_per_body
                            ]
                        )

            except HttpClientTimeoutError:
                result = set()
            except Exception:
                result = set()
            if cache is not None:
                cache[f"http:{_absolutize_url(url, target_url)}"] = result
            return result

        batches = await http_client.map(urls_to_fetch, _fetch_and_harvest)
        for ids in batches:
            identifiers |= ids

    return identifiers
