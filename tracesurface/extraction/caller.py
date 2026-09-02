from __future__ import annotations

import re

from tree_sitter import Node

from tracesurface.jsast import extract_string, node_text
from tracesurface.models import CallerInfo

_CALLER_RE = re.compile(r"^Object\((\w+)[.\[]")

_ESM_IMPORT_RE = re.compile(r'import\s*\{([^}]+)\}\s*from\s*["\'](\.[^"\']+)["\']')

_REQUIRE_CALL_BLACKLIST = frozenset(
    {
        "JSON",
        "URL",
        "URLSearchParams",
        "Number",
        "String",
        "Boolean",
        "Object",
        "Array",
        "Map",
        "Set",
        "Date",
        "Error",
        "Symbol",
        "RegExp",
        "Promise",
        "Function",
        "parseInt",
        "parseFloat",
        "parse",
        "stringify",
        "eval",
        "isNaN",
        "isFinite",
        "encodeURIComponent",
        "decodeURIComponent",
        "encodeURI",
        "decodeURI",
        "atob",
        "btoa",
        "if",
        "for",
        "while",
        "return",
        "switch",
        "typeof",
        "new",
        "await",
        "yield",
        "void",
        "delete",
    }
)


def is_webpack(source: str) -> bool:
    head = source[:2000]
    return (
        "webpackJsonp" in head
        or "__webpack_require__" in head
        or "webpackChunk" in head
    )


def find_module_id_webpack(node: Node) -> str:
    scope = node.parent
    while scope:
        if scope.type == "pair" and scope.parent and scope.parent.type == "object":
            key = scope.child_by_field_name("key")
            if key:
                mid = node_text(key).strip("\"'")
                if len(mid) <= 10:
                    return mid

        if scope.type in ("function_expression", "arrow_function"):
            parent = scope.parent
            if parent and parent.type == "array":
                for i, child in enumerate(parent.named_children):
                    if child.id == scope.id:
                        return f"#{i}"
        scope = scope.parent
    return ""


def extract_module_requires_webpack(root: Node) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}

    stack: list[tuple[Node, str]] = [(root, "")]
    while stack:
        node, current_module = stack.pop()

        if node.type == "pair" and node.parent and node.parent.type == "object":
            key = node.child_by_field_name("key")
            if key:
                mid = node_text(key).strip("\"'")
                if len(mid) <= 10:
                    current_module = mid

        if node.type in ("function_expression", "arrow_function"):
            parent = node.parent
            if parent and parent.type == "array":
                for i, child in enumerate(parent.named_children):
                    if child.id == node.id:
                        current_module = f"#{i}"
                        break

        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node and value_node and current_module:
                var_name = node_text(name_node)
                req_id = _extract_require_id_webpack(value_node)
                if req_id:
                    if current_module not in result:
                        result[current_module] = {}
                    result[current_module][var_name] = req_id

        children = list(node.children)
        for i in range(len(children) - 1, -1, -1):
            stack.append((children[i], current_module))

    return result


def _extract_require_id_webpack(value_node: Node) -> str:
    text = node_text(value_node)

    str_calls = [
        m.group(2)
        for m in re.finditer(r'\b(\w+)\("([^"]{2,10})"\)', text)
        if m.group(1) not in _REQUIRE_CALL_BLACKLIST
    ]
    if str_calls:
        return str_calls[-1]

    num_calls = [
        m.group(2)
        for m in re.finditer(r"\b(\w+)\((\d{1,5})\)", text)
        if m.group(1) not in _REQUIRE_CALL_BLACKLIST
    ]
    if num_calls:
        return f"#{num_calls[-1]}"

    return ""


def _extract_caller_prop_webpack(func_node: Node) -> str:
    if func_node.type != "call_expression":
        return ""
    args = func_node.child_by_field_name("arguments")
    if not args or not args.named_children:
        return ""

    inner = args.named_children[0]
    if inner.type == "member_expression":
        prop = inner.child_by_field_name("property")
        if prop:
            return node_text(prop)
    elif inner.type == "subscript_expression":
        index = inner.child_by_field_name("index")
        if index and index.type == "string":
            return extract_string(index) or ""
    return ""


def extract_esm_imports(source: str) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}

    for m in _ESM_IMPORT_RE.finditer(source):
        bindings_str, import_path = m.group(1), m.group(2)
        req_id = import_path.rsplit("/", 1)[-1]

        for binding in bindings_str.split(","):
            binding = binding.strip()
            if " as " in binding:
                original, local = binding.split(" as ", 1)
                original = original.strip()
                local = local.strip()
            else:
                original = local = binding.strip()
            if local and original:
                result[local] = (req_id, original)

    return result


def caller_info(
    node: Node,
    module_requires: dict[str, dict[str, str]],
    esm_imports: dict[str, tuple[str, str]],
    is_webpack_fmt: bool,
    js_url: str,
) -> CallerInfo:
    module_id = find_module_id_webpack(node) if is_webpack_fmt else js_url

    caller_var = ""
    caller_prop = ""
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        if func:
            func_text = node_text(func)

            if is_webpack_fmt:
                m = _CALLER_RE.match(func_text)
                if m:
                    caller_var = m.group(1)
                    caller_prop = _extract_caller_prop_webpack(func)

            if not caller_var and func.type == "member_expression":
                prop = func.child_by_field_name("property")
                if prop:
                    caller_prop = node_text(prop)
                obj = func.child_by_field_name("object")
                if obj:
                    obj_text = node_text(obj)

                    if is_webpack_fmt:
                        m2 = _CALLER_RE.match(obj_text)
                        if m2:
                            caller_var = m2.group(1)
                            webpack_prop = _extract_caller_prop_webpack(obj)
                            if webpack_prop:
                                caller_prop = webpack_prop

                    if not caller_var:
                        root = obj
                        while root.type == "member_expression":
                            next_obj = root.child_by_field_name("object")
                            if next_obj is None:
                                break
                            root = next_obj
                        if root.type == "identifier":
                            caller_var = node_text(root)

            if not caller_var and func.type == "identifier":
                caller_var = node_text(func)

    require_id = ""
    if caller_var:
        if is_webpack_fmt:
            if module_id:
                require_id = module_requires.get(module_id, {}).get(caller_var, "")
        elif caller_var in esm_imports:
            req_id, orig_name = esm_imports[caller_var]
            require_id = req_id
            if not caller_prop:
                caller_prop = orig_name

    return CallerInfo(
        module_id=module_id,
        caller_var=caller_var,
        caller_prop=caller_prop,
        require_id=require_id,
    )
