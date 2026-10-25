"""`CodeGraphBuilder` on top of graphify (https://github.com/Graphify-Labs/graphify).

graphify's extractor reads files from disk, so the snapshot's source files are written to a private
temporary directory (mode 0700) for the duration of one build and deleted right after; its AST
cache goes in the same directory. Only the structural (tree-sitter) pass runs: no LLM, no network,
no subprocess. Install with `uv sync --extra graph`.

graphify keeps per-run resolution caches in module globals, so builds are serialized by a lock.
"""

from __future__ import annotations

import tempfile
import threading
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

import networkx as nx
from graphify import analyze
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.detect import CODE_EXTENSIONS
from graphify.extract import extract

from agent_harness.domain.errors import CapabilityUnavailable
from agent_harness.ports.code_graph import Community, Direction, GraphEdge, GraphNode, Surprise

#: Manifests graphify reads to resolve imports (workspaces, module paths) besides CODE_EXTENSIONS.
MANIFESTS = {"pyproject.toml", "setup.cfg", "go.mod", "Cargo.toml"}
_BUILD_LOCK = threading.Lock()


class GraphifyCodeGraphBuilder:
    def __init__(self, *, max_files: int = 5_000) -> None:
        self._max_files = max_files

    def build(self, files: Mapping[str, str]) -> GraphifyCodeGraph:
        selected = sorted(p for p in files if _safe(p) and (PurePosixPath(p).suffix in CODE_EXTENSIONS or PurePosixPath(p).name in MANIFESTS))
        if len(selected) > self._max_files:
            raise CapabilityUnavailable(
                f"code graph is limited to {self._max_files} source files; this repository has {len(selected)} "
                "(raise CODE_GRAPH_MAX_FILES or use github_dependency_graph)"
            )
        with _BUILD_LOCK, tempfile.TemporaryDirectory(prefix="agent-harness-graph-") as tmp:
            root = Path(tmp).resolve()
            paths = []
            for rel in selected:
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(files[rel], encoding="utf-8")
                paths.append(target)
            extraction = extract(paths, root=root, cache_root=root, parallel=False)
            graph = build_from_json(extraction)
            _relativize(graph, root)
            communities = cluster(graph)
        return GraphifyCodeGraph(graph, communities)


class GraphifyCodeGraph:
    def __init__(self, graph: nx.Graph, communities: dict[int, list[str]]) -> None:
        self._g = graph
        self._communities = communities
        self._labels = _qualified_labels(graph)
        self._names: dict[str, list[str]] = defaultdict(list)
        for node_id, label in self._labels.items():
            for key in {_norm(label), _norm(label.rsplit(".", 1)[-1])}:
                self._names[key].append(node_id)

    def stats(self) -> dict[str, int]:
        files = {d.get("source_file") for _, d in self._g.nodes(data=True) if d.get("source_file")}
        return {"nodes": self._g.number_of_nodes(), "edges": self._g.number_of_edges(),
                "files": len(files), "communities": len(self._communities)}  # fmt: skip

    def node(self, node_id: str) -> GraphNode | None:
        if node_id not in self._g:
            return None
        data = self._g.nodes[node_id]
        return GraphNode(node_id, self._labels[node_id], data.get("source_file") or None, _line(data.get("source_location")))

    def find(self, query: str, limit: int = 20) -> list[GraphNode]:
        if query in self._g:
            return [self.node(query)]  # type: ignore[list-item]
        wanted = _norm(query)
        if not wanted:
            return []
        ranked: list[tuple[int, int, str]] = []
        for key, ids in self._names.items():
            rank = 0 if key == wanted else 1 if key.startswith(wanted) else 2 if wanted in key else None
            if rank is not None:
                ranked += [(rank, -self._g.degree(i), i) for i in ids]
        seen: list[str] = []
        for _, _, node_id in sorted(ranked):
            if node_id not in seen:
                seen.append(node_id)
        return [self.node(i) for i in seen[:limit]]  # type: ignore[misc]

    def neighbors(
        self, node_id: str, *, direction: Direction = "both", relation: str | None = None, limit: int = 50
    ) -> list[tuple[GraphEdge, GraphNode]]:
        if node_id not in self._g:
            return []
        out: list[tuple[GraphEdge, GraphNode]] = []
        for other in self._g.neighbors(node_id):
            edge = self._edge(node_id, other)
            if relation and edge.relation != relation:
                continue
            outgoing = edge.source == node_id
            if (direction == "out" and not outgoing) or (direction == "in" and outgoing):
                continue
            out.append((edge, self.node(other)))  # type: ignore[arg-type]
        out.sort(key=lambda pair: (pair[0].relation, -self._g.degree(pair[1].id), pair[1].label))
        return out[:limit]

    def path(self, source_id: str, target_id: str, max_hops: int = 8) -> list[GraphEdge] | None:
        if source_id not in self._g or target_id not in self._g:
            return None
        try:
            nodes = nx.shortest_path(self._g, source_id, target_id)
        except nx.NetworkXNoPath:
            return None
        if len(nodes) - 1 > max_hops:
            return None
        return [self._edge(a, b) for a, b in zip(nodes, nodes[1:], strict=False)]

    def hubs(self, limit: int = 10) -> list[tuple[GraphNode, int]]:
        return [(self.node(h["id"]), h["degree"]) for h in analyze.god_nodes(self._g, top_n=limit)]  # type: ignore[misc]

    def communities(self, limit: int = 10) -> list[Community]:
        result = []
        for cid, members in sorted(self._communities.items(), key=lambda kv: -len(kv[1]))[:limit]:
            top = sorted(members, key=lambda n: -self._g.degree(n))
            files = Counter(self._g.nodes[n].get("source_file") for n in members if self._g.nodes[n].get("source_file"))
            result.append(Community(cid, len(members), tuple(self.node(n) for n in top[:6]), tuple(f for f, _ in files.most_common(4))))  # type: ignore[misc]
        return result

    def import_cycles(self, limit: int = 10) -> list[list[str]]:
        return [list(c["cycle"]) for c in analyze.find_import_cycles(self._g, top_n=limit)]

    def surprises(self, limit: int = 5) -> list[Surprise]:
        found = analyze.surprising_connections(self._g, self._communities, top_n=limit)
        return [
            Surprise(s["source"], s["target"], s.get("relation", ""), s.get("confidence", ""), tuple(s.get("source_files") or ()), s.get("why", ""))
            for s in found
        ]  # fmt: skip

    def file_importance(self) -> dict[str, int]:
        scores: Counter[str] = Counter()
        for node_id, data in self._g.nodes(data=True):
            if data.get("source_file"):
                scores[data["source_file"]] += sum(1 for o in self._g.neighbors(node_id) if self._edge(node_id, o).relation != "contains")
        return dict(scores)

    def _edge(self, a: str, b: str) -> GraphEdge:
        data = self._g.edges[a, b]
        # graph.json is undirected; graphify keeps the real direction in _src/_tgt.
        return GraphEdge(
            data.get("_src", a), data.get("_tgt", b), data.get("relation", "related"), data.get("confidence", "EXTRACTED"),
            data.get("source_file") or None, _line(data.get("source_location")),
        )  # fmt: skip


def _safe(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not PurePosixPath(path).is_absolute() and ".." not in parts


def _relativize(graph: nx.Graph, root: Path) -> None:
    """Store paths relative to the repository. graphify anchors them at `root`, but falls back to the
    process cwd (`../../tmp/...`) for files it considers out of root. Runs while the files still exist."""

    def rel(value: object) -> str:
        if not isinstance(value, str) or not value:
            return ""
        path = Path(value)
        candidate = path if path.is_absolute() else root / path if (root / path).exists() else Path.cwd() / path
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except ValueError:
            return ""

    for _, data in graph.nodes(data=True):
        data["source_file"] = rel(data.get("source_file"))
    for _, _, data in graph.edges(data=True):
        data["source_file"] = rel(data.get("source_file"))


def _qualified_labels(graph: nx.Graph) -> dict[str, str]:
    """`.add()` -> `NoteStore.add()` (graphify labels methods relative to their class)."""
    labels = {n: d.get("label") or n for n, d in graph.nodes(data=True)}
    for a, b, data in graph.edges(data=True):
        if data.get("relation") == "method":
            owner, method = data.get("_src", a), data.get("_tgt", b)
            if labels[method].startswith("."):
                labels[method] = labels[owner] + labels[method]
    return labels


def _norm(name: str) -> str:
    return name.strip().removesuffix("()").lstrip(".").lower()


def _line(location: object) -> int | None:
    if isinstance(location, str) and location.startswith("L") and location[1:].split("-")[0].isdigit():
        return int(location[1:].split("-")[0])
    return None
