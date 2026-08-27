from __future__ import annotations

import re

from tracesurface.collection.artifacts.chunks.types import SourceDocument
from tracesurface.collection.artifacts.chunks.url_builder import (
    clean_relative_import,
    vite_asset_url,
)
from tracesurface.jsast import node_text, parse_js, walk_pre_iter

VITE_FINGERPRINT = "__vite__mapDeps"
VITE_FINGERPRINT_ALT = "__vite__fileDeps"


def discover_vite_urls(
    source: SourceDocument,
    base_url: str,
) -> frozenset[str]:
    tree_root = source.tree_root
    if tree_root is None:
        tree_root = parse_js(source.text).root_node

    urls: set[str] = set()

    if VITE_FINGERPRINT in source.text or VITE_FINGERPRINT_ALT in source.text:
        for node in walk_pre_iter(tree_root):
            if node.type != "identifier":
                continue
            if node_text(node) not in (VITE_FINGERPRINT, VITE_FINGERPRINT_ALT):
                continue

            parent = node.parent
            if not (parent and parent.type == "variable_declarator"):
                continue
            init = parent.child_by_field_name("value")
            if not (init and init.type == "array"):
                continue

            for child in init.named_children:
                if child.type != "string":
                    continue
                val = node_text(child).strip("'\"")
                if val.endswith(".js"):
                    urls.add(vite_asset_url(val, base_url))

    if source.url:
        for match in re.finditer(
            r'import\(\s*["\'](\.[^"\']+\.js)["\']',
            source.text,
        ):
            urls.add(clean_relative_import(source.url, match.group(1)))

    return frozenset(urls)
