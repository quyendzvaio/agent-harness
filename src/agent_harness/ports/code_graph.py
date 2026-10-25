"""Symbol-level code graph: classes, functions, methods and how they relate (calls, inherits, uses,
imports, contains), each relation labelled with how sure the extractor is.

The harness only defines the contract. The default implementation is `adapters/graphify`
(tree-sitter, 25+ languages); pass another one as `Overrides.code_graph_builder`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

Direction = Literal["in", "out", "both"]
#: EXTRACTED = stated in the source (import, direct call); INFERRED = deduced (e.g. call resolved
#: by name); AMBIGUOUS = several candidates. Only EXTRACTED may be claimed without reading the code.
Confidence = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    label: str
    file: str | None = None
    line: int | None = None

    @property
    def where(self) -> str:
        if not self.file:
            return "external"
        return f"{self.file}:L{self.line}" if self.line else self.file


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    relation: str
    confidence: str
    #: Where the relation is written (the call site, the import line, ...).
    file: str | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True)
class Community:
    id: int
    size: int
    members: tuple[GraphNode, ...]  # most connected first
    files: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Surprise:
    """A relation that crosses communities or file types: worth checking, often not obvious."""

    source: str  # labels
    target: str
    relation: str
    confidence: str
    files: tuple[str, ...]
    why: str


class CodeGraph(Protocol):
    def stats(self) -> dict[str, int]: ...
    def node(self, node_id: str) -> GraphNode | None: ...
    def find(self, query: str, limit: int = 20) -> list[GraphNode]:
        """Nodes whose name matches `query`: exact first, then prefix, then substring; ties by degree."""
        ...
    def neighbors(
        self, node_id: str, *, direction: Direction = "both", relation: str | None = None, limit: int = 50
    ) -> list[tuple[GraphEdge, GraphNode]]: ...
    def path(self, source_id: str, target_id: str, max_hops: int = 8) -> list[GraphEdge] | None: ...
    def hubs(self, limit: int = 10) -> list[tuple[GraphNode, int]]:
        """The most connected real abstractions (file nodes and method stubs excluded), with degree."""
        ...
    def communities(self, limit: int = 10) -> list[Community]: ...
    def import_cycles(self, limit: int = 10) -> list[list[str]]: ...
    def surprises(self, limit: int = 5) -> list[Surprise]: ...
    def file_importance(self) -> dict[str, int]:
        """Per source file: how connected its symbols are. Used to rank what to read next."""
        ...


class CodeGraphBuilder(Protocol):
    def build(self, files: Mapping[str, str]) -> CodeGraph:
        """CPU-bound and synchronous; callers run it in a worker thread. `files` maps repo paths to text."""
        ...
