from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from tree_sitter import Node

from tracesurface.extraction.aliases import build_aliases
from tracesurface.extraction.base_facts import extract_base_facts
from tracesurface.extraction.matcher_context import MatcherContext
from tracesurface.extraction.matchers.fetch_call import match_fetch_call
from tracesurface.extraction.matchers.member_method import match_member_method
from tracesurface.extraction.matchers.object_config import match_object_config
from tracesurface.extraction.matchers.split_wrapper import match_split_wrapper
from tracesurface.extraction.matchers.wrapped_call import match_wrapped_call
from tracesurface.extraction.matchers.xhr_open import match_xhr_open
from tracesurface.htmlast import extract_inline_scripts
from tracesurface.jsast import JsParser, walk_pre_iter
from tracesurface.models import ExtractionFacts, RequestFact

MatcherFn = Callable[[Node, MatcherContext], RequestFact | None]

MATCHERS: tuple[tuple[str, MatcherFn], ...] = (
    ("split-wrapper", match_split_wrapper),
    ("member-method", match_member_method),
    ("object-config", match_object_config),
    ("fetch-call", match_fetch_call),
    ("xhr-open", match_xhr_open),
    ("wrapped-call", match_wrapped_call),
)


def build_byte_to_char_offsets(source: str) -> list[list[int]]:
    table = []
    for line in source.split("\n"):
        byte_to_char = []
        char_idx = 0

        for ch in line:
            for _ in range(len(ch.encode("utf-8"))):
                byte_to_char.append(char_idx)
            char_idx += 1

        byte_to_char.append(char_idx)
        table.append(byte_to_char)
    return table


def byte_col_to_char_col(table: list[list[int]], line: int, byte_col: int) -> int:
    if line >= len(table):
        return table[-1][-1]
    row = table[line]
    if byte_col >= len(row):
        return row[-1]
    return row[byte_col]


def _file_facts(matches: list[RequestFact], root, js_url, ctx) -> ExtractionFacts:
    return ExtractionFacts(
        requests=tuple(matches),
        bases=tuple(extract_base_facts(root, js_url, ctx)),
        aliases=tuple(build_aliases(root, ctx)),
    )


class ASTAnalyzer:
    def __init__(self) -> None:
        self.parser = JsParser()

    def _collect_matches(
        self,
        root: Node,
        source: str,
        js_url: str,
        wrapper_prefixes: dict[str, str] | None = None,
    ):
        byte_to_char = build_byte_to_char_offsets(source)

        from tracesurface.extraction.caller import (
            caller_info,
            extract_esm_imports,
            extract_module_requires_webpack,
            is_webpack,
        )

        is_webpack_fmt = is_webpack(source)
        module_requires: dict[str, dict[str, str]] = {}
        esm_imports: dict[str, tuple[str, str]] = {}

        if is_webpack_fmt:
            module_requires = extract_module_requires_webpack(root)
        else:
            esm_imports = extract_esm_imports(source)

        from tracesurface.extraction.resolve import ResolveCtx, request_client_ref
        from tracesurface.extraction.scope import build_scope_index

        ctx = ResolveCtx(
            scope_index=build_scope_index(root),
            is_webpack=is_webpack_fmt,
            js_url=js_url,
            module_requires=module_requires,
            esm_imports=esm_imports,
        )
        matcher_ctx = MatcherContext(
            ctx, wrapper_prefixes=wrapper_prefixes or {}
        )

        matches: list[RequestFact] = []
        seen: set[str] = set()

        for node in walk_pre_iter(root):
            for name, fn in MATCHERS:
                result = fn(node, matcher_ctx)
                if result is None:
                    continue

                loc = result.location
                col_start = byte_col_to_char_col(byte_to_char, loc.line, loc.col_start)
                col_end = byte_col_to_char_col(byte_to_char, loc.line, loc.col_end)
                client_ref = request_client_ref(node, ctx)
                fact = replace(
                    result,
                    request_id=f"{js_url}:{loc.line}:{col_start}",
                    pattern=name,
                    location=replace(
                        loc, url=js_url, col_start=col_start, col_end=col_end
                    ),
                    caller=caller_info(
                        node,
                        module_requires,
                        esm_imports,
                        is_webpack_fmt,
                        js_url,
                    ),
                    client_refs=(client_ref,) if client_ref is not None else (),
                )

                dedup_key = f"{fact.path}:{fact.location.line}:{fact.location.col_start}"
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    matches.append(fact)
                break

        return matches, ctx

    def analyze_parsed(
        self,
        root: Node,
        source: str,
        js_url: str = "",
        wrapper_prefixes: dict[str, str] | None = None,
        line_offset: int = 0,
    ) -> ExtractionFacts:
        matches, ctx = self._collect_matches(root, source, js_url, wrapper_prefixes)
        if line_offset:
            shifted: list[RequestFact] = []
            for match in matches:
                line = match.location.line + line_offset
                shifted.append(
                    replace(
                        match,
                        location=replace(match.location, line=line),
                        request_id=f"{js_url}:{line}:{match.location.col_start}",
                    )
                )
            matches = shifted
        return _file_facts(matches, root, js_url, ctx)

    def analyze_js_all(
        self,
        source: str,
        js_url: str = "",
        wrapper_prefixes: dict[str, str] | None = None,
    ):
        source = self.parser.normalize(source)
        tree = self.parser.parse(source)
        return self.analyze_parsed(
            tree.root_node, source, js_url, wrapper_prefixes
        )

    def analyze_html_inline_all(
        self,
        html: str,
        js_url: str = "",
        wrapper_prefixes: dict[str, str] | None = None,
    ):
        facts_acc = ExtractionFacts()
        for start_line, script in extract_inline_scripts(html):
            norm = self.parser.normalize(script)
            tree = self.parser.parse(norm)
            file_facts = self.analyze_parsed(
                tree.root_node,
                norm,
                js_url,
                wrapper_prefixes,
                line_offset=start_line,
            )
            facts_acc = ExtractionFacts(
                requests=facts_acc.requests + file_facts.requests,
                bases=facts_acc.bases + file_facts.bases,
                aliases=facts_acc.aliases + file_facts.aliases,
            )
        return facts_acc
