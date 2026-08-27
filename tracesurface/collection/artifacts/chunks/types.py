from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node


@dataclass(frozen=True, slots=True)
class SourceDocument:
    url: str
    text: str
    tree_root: Node | None = None


@dataclass(frozen=True, slots=True)
class ChunkEvalPlan:
    function: str
    params: tuple[int | str, ...]
