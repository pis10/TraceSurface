from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from tree_sitter import Node

from tracesurface.extraction.resolve import (
    ResolveCtx,
    render_prod_url,
    resolve_template,
    template_to_expr_path,
)
from tracesurface.jsast import (
    extract_literal_value,
    extract_string,
    get_object_props,
    node_text,
)
from tracesurface.models import (
    CallerInfo,
    Lit,
    Param,
    RequestFact,
    SourceLocation,
    UrlTemplate,
)
from tracesurface.policies import DEFAULT_STATIC_RESOURCE_EXTS, ThirdPartyPolicy

EXPR = "EXPR"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_THIRD_PARTY_POLICY = ThirdPartyPolicy()


@dataclass(frozen=True, slots=True)
class RequestOptionSlots:
    method_keys: tuple[str, ...] = ("method", "type")
    body_keys: tuple[str, ...] = ("body", "data")
    query_key: str = "params"
    base_keys: tuple[str, ...] = ("baseURL", "baseUrl")
    axios_config_keys: frozenset[str] = frozenset(
        {
            "headers",
            "timeout",
            "withCredentials",
            "responseType",
            "baseURL",
            "auth",
            "signal",
            "cancelToken",
            "validateStatus",
            "params",
        }
    )


REQUEST_OPTION_SLOTS = RequestOptionSlots()


@dataclass(frozen=True, slots=True)
class CallSkipRules:
    funcs: frozenset[str]
    prefixes: tuple[str, ...]


WRAPPED_CALL_SKIP_RULES = CallSkipRules(
    funcs=frozenset(
        {
            "require",
            "import",
            "define",
            "resolve",
            "reject",
            "createelement",
            "getelementbyid",
            "queryselector",
            "log",
            "warn",
            "error",
            "info",
            "debug",
            "push",
            "replace",
            "assign",
            "open",
            "navigate",
            "goto",
            "redirect",
            "setitem",
            "getitem",
            "removeitem",
            "settimeout",
            "setinterval",
            "join",
            "split",
            "match",
            "test",
            "exec",
            "indexof",
            "includes",
            "concat",
            "startswith",
            "endswith",
            "stringify",
            "parse",
            "fetch",
            "comment",
        }
    ),
    prefixes=("navigate", "goto", "redirect", "route", "locate"),
)

OBJECT_CONFIG_SKIP_RULES = CallSkipRules(
    funcs=frozenset(
        {
            "navigateto",
            "switchtab",
            "redirectto",
            "relaunch",
            "navigateback",
        }
    ),
    prefixes=("navigate", "redirect", "route", "goto", "locate"),
)


@dataclass(frozen=True, slots=True)
class MatcherContext:
    resolve_ctx: ResolveCtx
    wrapper_prefixes: Mapping[str, str] = field(default_factory=dict)

    def collapse_string(self, node: Node | None) -> str:
        if node is None:
            return EXPR
        tmpl = resolve_template(node, self.resolve_ctx)
        url = render_prod_url(tmpl)
        return url if url is not None else template_to_expr_path(tmpl)

    def make_match(
        self,
        node: Node,
        *,
        path: str,
        method: str,
        params: list[Param],
        url_node: Node | None = None,
    ) -> RequestFact:
        if method and method.lower() in HTTP_METHODS:
            final_method = method.upper()
        else:
            final_method = "UNKNOWN"

        template = (
            resolve_template(url_node, self.resolve_ctx)
            if url_node is not None
            else None
        )
        if template is None:
            template = UrlTemplate((Lit(path),))

        return RequestFact(
            request_id="",
            method=final_method,
            path=path,
            url_template=template,
            client_refs=(),
            params=tuple(params),
            location=SourceLocation(
                url="",
                line=node.start_point[0],
                col_start=node.start_point[1],
                col_end=node.end_point[1],
            ),
            caller=CallerInfo(),
            pattern="",
        )


def unwrap_json_stringify(node: Node | None) -> Node | None:
    if not node or node.type != "call_expression":
        return node
    func = node.child_by_field_name("function")
    if not func or func.type != "member_expression":
        return node
    prop = func.child_by_field_name("property")
    if not prop or node_text(prop) != "stringify":
        return node

    args = node.child_by_field_name("arguments")
    if args and args.named_children:
        return args.named_children[0]
    return node


def extract_params(node: Node | None, location: str) -> list[Param]:
    props = get_object_props(node)
    return [
        Param(name=key, location=location, default=extract_literal_value(val))
        for key, val in props.items()
    ]


def _first_prop(props: dict[str, Node], keys: tuple[str, ...]) -> Node | None:
    for key in keys:
        node = props.get(key)
        if node is not None:
            return node
    return None


def parse_fetch_options(node: Node | None) -> tuple[str, list[Param]]:
    if not node or node.type != "object":
        return "UNKNOWN", []

    props = get_object_props(node)
    slots = REQUEST_OPTION_SLOTS

    method = "UNKNOWN"
    method_node = _first_prop(props, slots.method_keys)
    if method_node:
        raw = extract_string(method_node)
        if raw and raw.lower() in HTTP_METHODS:
            method = raw.upper()

    params: list[Param] = []

    body_node = _first_prop(props, slots.body_keys)
    if body_node:
        params.extend(extract_params(unwrap_json_stringify(body_node), "body"))
    if slots.query_key in props and props[slots.query_key]:
        params.extend(extract_params(props[slots.query_key], "query"))

    return method, params


def parse_axios_args(second_arg: Node | None, http_method: str) -> list[Param]:
    if not second_arg or second_arg.type != "object":
        return []

    props = get_object_props(second_arg)
    slots = REQUEST_OPTION_SLOTS
    result: list[Param] = []

    if any(k in props for k in slots.axios_config_keys):
        body_node = props.get("data")
        if body_node:
            result.extend(extract_params(body_node, "body"))
        if slots.query_key in props and props[slots.query_key]:
            result.extend(extract_params(props[slots.query_key], "query"))
    else:
        location = (
            "body" if http_method.lower() in ("post", "put", "patch") else "query"
        )
        result.extend(extract_params(second_arg, location))

    return result


def get_callee_name(func_node: Node | None) -> str:
    if not func_node:
        return ""

    if func_node.type == "call_expression":
        inner_args = func_node.child_by_field_name("arguments")
        if inner_args and inner_args.named_children:
            return get_callee_name(inner_args.named_children[0])

    if func_node.type == "member_expression":
        prop = func_node.child_by_field_name("property")
        return node_text(prop).lower() if prop else ""
    if func_node.type == "identifier":
        return node_text(func_node).lower()
    return ""


_NON_API_EXTS = DEFAULT_STATIC_RESOURCE_EXTS | {
    ".html",
    ".htm",
    ".shtml",
    ".yaml",
    ".yml",
}

_MIME_PREFIXES = (
    "application/",
    "text/",
    "image/",
    "audio/",
    "video/",
    "multipart/",
    "font/",
    "model/",
    "chemical/",
)


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0

    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def maybe_url(s: str) -> bool:
    if not s or s == EXPR:
        return False
    if not s.replace(EXPR, ""):
        return False
    if "/" not in s:
        return False

    path_part = s.split("?", 1)[0]
    if any(c in path_part for c in " ()!<>{}^$@="):
        return False

    if any(s.startswith(prefix) for prefix in _MIME_PREFIXES):
        return False

    clean = s.replace(EXPR, "").strip("/")
    if len(clean) < 2:
        return False

    segments = [seg for seg in clean.split("/") if seg]
    if segments and not any(len(seg) >= 3 for seg in segments):
        return False

    if clean and len(clean) > 8 and _shannon_entropy(clean) >= 4.9:
        return False

    clean_ext = s.rstrip("/").split("?")[0].split("#")[0]
    if any(clean_ext.endswith(ext) for ext in _NON_API_EXTS):
        return False

    if s.startswith("http"):
        try:
            parsed = urlparse(s.split(EXPR)[0] + s.split(EXPR)[-1] if EXPR in s else s)
        except ValueError:
            return False
        path = parsed.path.strip("/")
        if not path:
            return False
        if "w3.org" in s:
            return False
        host = parsed.hostname or ""
        if _THIRD_PARTY_POLICY.is_third_party("http://" + host + "/"):
            return False
        return True

    if s.startswith("/") or s.startswith(EXPR):
        return True

    if "." in s and "/" in s:
        return True
    return False
