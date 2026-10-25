from __future__ import annotations

import asyncio
import fnmatch
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol

from agent_harness.domain.errors import CapabilityUnavailable, InvalidInput, ToolFailed
from agent_harness.harness.observability.logger import get_logger
from agent_harness.ports.code_graph import CodeGraph, CodeGraphBuilder
from agent_harness.sources.github.architecture_analyzer import ArchitectureSummary, analyze
from agent_harness.sources.github.dependency_graph import DependencyGraph, build_dependency_graph
from agent_harness.sources.github.detector import GithubTarget
from agent_harness.sources.github.snapshot import RepositorySnapshot
from agent_harness.sources.github.system_map import SystemMap, build_system_map

log = get_logger(__name__)

KEY_FILES = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml",
    "Makefile", "Procfile",
)  # fmt: skip
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs",
                   ".cpp", ".cc", ".c", ".h", ".swift", ".scala")  # fmt: skip
MAX_KEY_FILE_CHARS = 6_000
MAX_README_CHARS = 12_000
MAX_READ_LINES = 400
MAX_TREE_ENTRIES = 400
MAX_SEARCH_RESULTS = 60


class GithubApi(Protocol):
    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]: ...
    async def get_tree(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]: ...
    async def get_file(self, owner: str, repo: str, path: str, ref: str | None = None) -> str: ...
    async def get_tarball(self, owner: str, repo: str, ref: str, *, max_bytes: int) -> bytes: ...


@dataclass(slots=True)
class RepoOverview:
    target: GithubTarget
    ref: str
    meta: dict[str, Any]
    readme_path: str | None
    readme: str
    key_files: dict[str, str] = field(default_factory=dict)
    summary: ArchitectureSummary = field(default_factory=ArchitectureSummary)
    complete: bool = False  # True when built from a full snapshot


@dataclass(frozen=True, slots=True)
class FileSlice:
    path: str
    start: int
    end: int
    total_lines: int
    numbered: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    line: int
    start: int
    end: int
    snippet: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: list[SearchHit]
    total_matches: int
    files_searched: int


@dataclass(frozen=True, slots=True)
class Coverage:
    source_files: list[str]
    read: dict[str, int]  # path -> lines read
    lines: dict[str, int]  # path -> total lines

    @property
    def read_ratio(self) -> float:
        return len(self.read) / len(self.source_files) if self.source_files else 1.0


class RepositoryReader:
    """Reads a repository through one in-memory snapshot (tarball); falls back to per-file API calls
    when the archive is too large. Snapshots are cached per (repo, ref) in-process."""

    def __init__(
        self,
        client: GithubApi,
        *,
        snapshot_max_bytes: int = 60_000_000,
        cache_size: int = 8,
        cache_ttl_s: float = 900,
        code_graph_builder: CodeGraphBuilder | None = None,
        code_graph_timeout_s: float = 180,
        system_map_max_files: int = 3_000,
    ) -> None:
        self._client = client
        self._snapshot_max_bytes = snapshot_max_bytes
        self._cache_size = cache_size
        self._cache_ttl_s = cache_ttl_s
        self._meta: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._snapshots: OrderedDict[tuple[str, str, str], tuple[float, RepositorySnapshot | None]] = OrderedDict()
        self._graphs: dict[tuple[str, str, str], DependencyGraph] = {}
        self._system_maps: dict[tuple[str, str, str], SystemMap] = {}
        self._system_map_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._code_graph_builder = code_graph_builder
        self._code_graph_timeout_s = code_graph_timeout_s
        self._system_map_max_files = system_map_max_files
        self._code_graphs: dict[tuple[str, str, str], asyncio.Task[CodeGraph]] = {}
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    # --- resolution & snapshot ----------------------------------------------------------------

    async def resolve(self, target: GithubTarget) -> tuple[str, dict[str, Any]]:
        key = (target.owner.lower(), target.repo.lower())
        cached = self._meta.get(key)
        if cached and cached[0] > time.monotonic():
            meta = cached[1]
        else:
            meta = await self._client.get_repo(target.owner, target.repo)
            self._meta[key] = (time.monotonic() + self._cache_ttl_s, meta)
        return target.ref or meta.get("default_branch") or "main", meta

    async def snapshot(self, target: GithubTarget) -> RepositorySnapshot | None:
        ref, _ = await self.resolve(target)
        key = (target.owner.lower(), target.repo.lower(), ref)
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._snapshots.get(key)
            if cached and cached[0] > time.monotonic():
                self._snapshots.move_to_end(key)
                return cached[1]
            try:
                data = await self._client.get_tarball(target.owner, target.repo, ref, max_bytes=self._snapshot_max_bytes)
                snap: RepositorySnapshot | None = RepositorySnapshot.from_tarball(data, ref)
            except ToolFailed as exc:
                log.warning("snapshot_unavailable", extra={"repo": f"{target.owner}/{target.repo}", "reason": exc.message})
                snap = None
            self._snapshots[key] = (time.monotonic() + self._cache_ttl_s, snap)
            self._forget(key)
            while len(self._snapshots) > self._cache_size:
                evicted, _ = self._snapshots.popitem(last=False)
                self._forget(evicted)
            return snap

    def _forget(self, key: tuple[str, str, str]) -> None:
        self._graphs.pop(key, None)
        self._system_maps.pop(key, None)
        task = self._code_graphs.pop(key, None)
        if task is not None and not task.done():
            task.cancel()  # the worker thread finishes on its own; only the result is dropped

    async def _require_snapshot(self, target: GithubTarget, what: str) -> RepositorySnapshot:
        snap = await self.snapshot(target)
        if snap is None:
            raise CapabilityUnavailable(f"{what} needs a repository snapshot, but this repository is too large to download")
        return snap

    async def _tree(self, target: GithubTarget) -> tuple[str, list[dict[str, Any]]]:
        snap = await self.snapshot(target)
        if snap is not None:
            return snap.ref, snap.tree()
        ref, _ = await self.resolve(target)
        return ref, await self._client.get_tree(target.owner, target.repo, ref)

    async def _read(self, target: GithubTarget, path: str) -> str:
        snap = await self.snapshot(target)
        if snap is not None:
            if path in snap.files:
                return snap.files[path]
            if path in snap.skipped:
                raise InvalidInput(f"'{path}' cannot be read: {snap.skipped[path]}")
            raise InvalidInput(f"GitHub resource not found (or private): {path}")
        ref, _ = await self.resolve(target)
        return await self._client.get_file(target.owner, target.repo, path, ref)

    # --- operations used by tools -----------------------------------------------------------------

    async def overview(self, target: GithubTarget) -> RepoOverview:
        ref, meta = await self.resolve(target)
        snap = await self.snapshot(target)
        _, tree = await self._tree(target)
        paths = {item["path"] for item in tree if item["type"] == "blob"}
        readme_path = next((p for p in sorted(paths) if "/" not in p and p.lower().startswith("readme")), None)
        readme = (await self._read(target, readme_path))[:MAX_README_CHARS] if readme_path else ""
        key_files: dict[str, str] = {}
        for name in KEY_FILES:
            if name in paths:
                key_files[name] = (await self._read(target, name))[:MAX_KEY_FILE_CHARS]
        contents = snap.files if snap is not None else key_files
        return RepoOverview(target, ref, meta, readme_path, readme, key_files, analyze(tree, contents), snap is not None)

    async def list_tree(self, target: GithubTarget, path: str = "", max_depth: int = 3) -> tuple[str, list[str]]:
        ref, tree = await self._tree(target)
        prefix = path.strip("/")
        base_depth = len(PurePosixPath(prefix).parts) if prefix else 0
        lines: list[str] = []
        for item in sorted(tree, key=lambda i: i["path"]):
            item_path = item["path"]
            if prefix and not (item_path == prefix or item_path.startswith(prefix + "/")):
                continue
            depth = len(PurePosixPath(item_path).parts) - base_depth
            if depth < 1 or depth > max_depth:
                continue
            suffix = "/" if item["type"] == "tree" else ""
            size = f"  ({item['size']} B)" if item.get("size") else ""
            lines.append(f"{'  ' * (depth - 1)}{item_path}{suffix}{size}")
        if not lines:
            raise InvalidInput(f"path '{path}' not found in the repository tree")
        if len(lines) > MAX_TREE_ENTRIES:
            lines = lines[:MAX_TREE_ENTRIES] + [f"... ({len(lines) - MAX_TREE_ENTRIES} more entries, narrow the path)"]
        return ref, lines

    async def read_file(self, target: GithubTarget, path: str, start: int = 1, end: int | None = None) -> FileSlice:
        path = path.strip("/")
        lines = (await self._read(target, path)).splitlines()
        total = len(lines)
        start = max(1, start)
        end = min(total, end or start + MAX_READ_LINES - 1, start + MAX_READ_LINES - 1)
        if total and start > total:
            raise InvalidInput(f"start line {start} is past the end of the file ({total} lines)")
        numbered = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1)) if total else ""
        return FileSlice(path, start, end, total, numbered)

    async def search(
        self, target: GithubTarget, query: str, *, regex: bool = False, path_glob: str = "", case_sensitive: bool = False,
        context: int = 1, max_results: int = 30,
    ) -> SearchResult:  # fmt: skip
        snap = await self._require_snapshot(target, "code search")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query if regex else re.escape(query), flags)
        except re.error as exc:
            raise InvalidInput(f"invalid regular expression: {exc}") from exc
        limit = min(max_results, MAX_SEARCH_RESULTS)
        hits: list[SearchHit] = []
        total = searched = 0
        for path in sorted(snap.files):
            if path_glob and not fnmatch.fnmatch(path, path_glob):
                continue
            searched += 1
            lines = snap.files[path].splitlines()
            for number, line in enumerate(lines, start=1):
                if not pattern.search(line):
                    continue
                total += 1
                if len(hits) < limit:
                    lo, hi = max(1, number - context), min(len(lines), number + context)
                    snippet = "\n".join(f"{n:>5}{'>' if n == number else ' '} {lines[n - 1][:300]}" for n in range(lo, hi + 1))
                    hits.append(SearchHit(path, number, lo, hi, snippet))
        return SearchResult(hits, total, searched)

    async def dependency_graph(self, target: GithubTarget) -> DependencyGraph:
        snap = await self._require_snapshot(target, "dependency analysis")
        key = (target.owner.lower(), target.repo.lower(), snap.ref)
        if key not in self._graphs:
            self._graphs[key] = await asyncio.to_thread(build_dependency_graph, snap.files)
        return self._graphs[key]

    async def system_map(self, target: GithubTarget) -> SystemMap:
        """Declared runtime topology (manifests, channels, data ownership), built once per snapshot."""
        snap = await self._require_snapshot(target, "the system map")
        key = (target.owner.lower(), target.repo.lower(), snap.ref)
        async with self._system_map_locks.setdefault(key, asyncio.Lock()):  # two tools asking at once build it once
            if key not in self._system_maps:
                self._system_maps[key] = await asyncio.to_thread(build_system_map, snap.files, max_files=self._system_map_max_files)
            return self._system_maps[key]

    async def coverage(self, target: GithubTarget, read_ranges: dict[str, set[int]]) -> Coverage:
        snap = await self._require_snapshot(target, "coverage")
        sources = sorted(p for p in snap.files if p.endswith(SOURCE_SUFFIXES))
        line_counts = {p: snap.files[p].count("\n") + 1 for p in sources}
        read = {p: len(lines) for p, lines in read_ranges.items() if p in line_counts}
        return Coverage(sources, read, line_counts)

    @property
    def has_code_graph(self) -> bool:
        return self._code_graph_builder is not None

    async def code_graph(self, target: GithubTarget) -> CodeGraph:
        """Symbol-level graph, built once per snapshot in a worker thread. A build that outlives the
        timeout keeps running; the next call picks up its result."""
        if self._code_graph_builder is None:
            raise CapabilityUnavailable(
                "the symbol-level code graph is not installed on this server (extra 'graph'); "
                "use github_dependency_graph and github_search_code instead"
            )
        snap = await self._require_snapshot(target, "code graph")
        key = (target.owner.lower(), target.repo.lower(), snap.ref)
        task = self._code_graphs.get(key)
        if task is None:
            task = asyncio.ensure_future(asyncio.to_thread(self._code_graph_builder.build, snap.files))
            self._code_graphs[key] = task
        try:
            return await asyncio.wait_for(asyncio.shield(task), self._code_graph_timeout_s)
        except TimeoutError as exc:
            raise ToolFailed(
                f"the code graph is still being built after {self._code_graph_timeout_s:.0f}s; "
                "continue with other tools and call again later"
            ) from exc
        except (CapabilityUnavailable, ToolFailed, InvalidInput):
            raise
        except Exception as exc:
            log.exception("code_graph_failed", extra={"repo": f"{target.owner}/{target.repo}"})
            raise ToolFailed(f"code graph extraction failed: {type(exc).__name__}") from exc

    async def built_code_graph(self, target: GithubTarget) -> CodeGraph | None:
        """The code graph if it has already been built successfully; never starts a build."""
        ref, _ = await self.resolve(target)
        task = self._code_graphs.get((target.owner.lower(), target.repo.lower(), ref))
        if task is None or not task.done() or task.cancelled() or task.exception() is not None:
            return None
        return task.result()
