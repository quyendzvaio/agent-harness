"""Source-reading tools. Each tool registers what it read as evidence and labels it `[E#]`."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from agent_harness.domain.errors import InvalidInput
from agent_harness.domain.sources import SourceKind, SourceRef
from agent_harness.harness.context import RunContext
from agent_harness.harness.tools.spec import SOURCE, ToolOutput, ToolSpec
from agent_harness.ports.code_graph import CodeGraph, GraphEdge, GraphNode
from agent_harness.research.chunking import chunk_lines, chunk_text_by_chars
from agent_harness.sources.blog.detector import blog_source
from agent_harness.sources.blog.fetcher import BlogFetcher
from agent_harness.sources.github.detector import GithubTarget, parse_github_url
from agent_harness.sources.github.repository_reader import RepositoryReader
from agent_harness.sources.registry import SourceRegistry


class RepoInput(BaseModel):
    url: str = Field(description="GitHub repository URL, e.g. https://github.com/owner/repo")


class TreeInput(RepoInput):
    path: str = Field(default="", description="Directory inside the repo; empty for the root")
    max_depth: int = Field(default=3, ge=1, le=6)


class FileInput(RepoInput):
    path: str = Field(description="File path inside the repo, e.g. src/app/main.py")
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1, description="Inclusive; at most 400 lines are returned")


class SearchInput(RepoInput):
    query: str = Field(min_length=1, max_length=200, description="Text to find (or a regex when regex=true)")
    regex: bool = False
    path_glob: str = Field(default="", description="Restrict to files matching a glob, e.g. 'src/**/*.py' or '*.ts'")
    case_sensitive: bool = False
    max_results: int = Field(default=30, ge=1, le=60)


class GraphInput(RepoInput):
    scope: str = Field(default="", description="Only show packages/files under this directory")
    level: Literal["package", "file"] = Field(default="package", description="package = directory level, file = file level")


class CodeGraphInput(RepoInput):
    op: Literal["overview", "find", "neighbors", "path", "cycles"] = Field(
        default="overview",
        description="overview = core abstractions, communities, cycles, surprising links; find = locate a symbol; "
        "neighbors = what a symbol calls/uses/inherits and who calls/uses/inherits it; path = how symbol reaches target; "
        "cycles = import cycles",
    )
    symbol: str = Field(default="", max_length=200, description="Class/function/method/file name (e.g. NoteStore.add) or a node id from a previous result")
    target: str = Field(default="", max_length=200, description="op=path only: the symbol to reach")
    direction: Literal["in", "out", "both"] = Field(default="both", description="neighbors: in = callers/users/importers of the symbol, out = what it calls/uses")
    relation: str = Field(default="", max_length=40, description="neighbors: keep one relation, e.g. calls, inherits, uses, imports_from, method, contains")
    limit: int = Field(default=30, ge=1, le=100)


class ArticleInput(BaseModel):
    url: str = Field(description="URL of the blog post / article")


class _Labels:
    def __init__(self, ctx: RunContext, source: SourceRef) -> None:
        self._ctx = ctx
        self._source = source
        self.labels: list[str] = []

    def add(self, locator: str, text: str) -> str:
        label = self._ctx.evidence.add(self._source, locator, text).label
        self.labels.append(label)
        return label


_READ_LOCATOR = re.compile(r"^(?P<path>.+):L(?P<start>\d+)-L(?P<end>\d+)$")


def read_ranges(ctx: RunContext, canonical_id: str) -> dict[str, set[int]]:
    """Lines of each file actually read with github_read_file in this conversation (search hits excluded)."""
    ranges: dict[str, set[int]] = {}
    for item in ctx.evidence.items():
        if item.source.canonical_id != canonical_id:
            continue
        match = _READ_LOCATOR.match(item.locator)
        if match:
            ranges.setdefault(match["path"], set()).update(range(int(match["start"]), int(match["end"]) + 1))
    return ranges


_CONFIDENCE_NOTE = "Confidence: EXTRACTED = written in the code; INFERRED/AMBIGUOUS = deduced by the extractor — read the code before stating it as fact."


def _label(graph: CodeGraph, node_id: str) -> str:
    node = graph.node(node_id)
    return node.label if node else node_id


def _edge_text(graph: CodeGraph, edge: GraphEdge) -> str:
    where = f" at {edge.file}:L{edge.line}" if edge.file and edge.line else f" in {edge.file}" if edge.file else ""
    return f"{_label(graph, edge.source)} --{edge.relation}--> {_label(graph, edge.target)} [{edge.confidence}]{where}"


def _edge_locator(edge: GraphEdge) -> str:
    # Not `path:Lx-Ly`: a graph fact must not count as having read the file (see read_ranges).
    return f"{edge.file}:L{edge.line} (graph: {edge.relation})" if edge.file and edge.line else f"graph:{edge.source}->{edge.target} ({edge.relation})"


def _resolve_symbol(graph: CodeGraph, name: str) -> tuple[GraphNode, list[GraphNode]]:
    matches = graph.find(name, limit=6)
    if not matches:
        raise InvalidInput(f"no symbol matching {name!r} in the code graph; try op=find with a shorter name, or github_search_code")
    return matches[0], matches[1:]


def _graph_overview(graph: CodeGraph, limit: int) -> str:
    stats = graph.stats()
    lines = [
        f"Symbol graph (tree-sitter structural extraction): {stats['nodes']} nodes, {stats['edges']} edges, "
        f"{stats['files']} files, {stats['communities']} communities.",
        _CONFIDENCE_NOTE,
        "Core abstractions (most connected): "
        + (", ".join(f"{n.label} ({n.where}, degree {d})" for n, d in graph.hubs(min(limit, 15))) or "none"),
        "Communities (groups of tightly connected code, largest first):",
    ]
    for community in graph.communities(min(limit, 12)):
        members = ", ".join(n.label for n in community.members)
        lines.append(f"  #{community.id} ({community.size} nodes): {members} — files: {', '.join(community.files) or '-'}")
    cycles = graph.import_cycles(10)
    lines.append("Import cycles: " + ("; ".join(" → ".join(c + c[:1]) for c in cycles) if cycles else "none"))
    surprises = graph.surprises(5)
    if surprises:
        lines.append("Surprising connections (worth checking):")
        lines += [f"  {s.source} --{s.relation}--> {s.target} [{s.confidence}] ({', '.join(s.files)}): {s.why}" for s in surprises]
    lines.append("Next: op=neighbors symbol=<name> to follow calls/inheritance, op=path symbol=<a> target=<b> to trace a flow.")
    return "\n".join(lines)


def build_source_tools(*, sources: SourceRegistry, github: RepositoryReader, blog: BlogFetcher) -> list[ToolSpec]:
    def github_target(url: str) -> GithubTarget:
        sources.require(SourceKind.GITHUB)
        target = parse_github_url(url)
        if target is None:
            raise InvalidInput(f"not a GitHub repository URL: {url}")
        return target

    async def repo_overview(args: RepoInput, ctx: RunContext) -> ToolOutput:
        target = github_target(args.url)
        overview = await github.overview(target)
        ev = _Labels(ctx, target.source)
        meta = overview.meta
        parts = [
            f"Repository {target.owner}/{target.repo} @ {overview.ref}",
            f"Description: {meta.get('description') or '-'} | stars: {meta.get('stargazers_count', '?')} | "
            f"license: {(meta.get('license') or {}).get('spdx_id', '-')}",
            "Snapshot: full repository loaded (search, dependency graph and coverage available)"
            if overview.complete
            else "Snapshot: repository too large to download; reading file by file (no search/dependency graph)",
            "",
            f"[{ev.add('tree:/ (static analysis)', overview.summary.render())}] Structure summary (computed from the file tree):",
            overview.summary.render(),
        ]
        if overview.readme_path:
            for chunk in chunk_text_by_chars(overview.readme, locator_prefix=overview.readme_path):
                parts += ["", f"[{ev.add(chunk.locator, chunk.text)}] {chunk.locator}", chunk.text]
        for path, content in overview.key_files.items():
            lines = content.splitlines()
            locator = f"{path}:L1-L{len(lines)}"
            parts += ["", f"[{ev.add(locator, content)}] {locator}", content]
        return ToolOutput("\n".join(parts), tuple(ev.labels))

    async def list_tree(args: TreeInput, ctx: RunContext) -> ToolOutput:
        target = github_target(args.url)
        ref, lines = await github.list_tree(target, args.path, args.max_depth)
        listing = "\n".join(lines)
        ev = _Labels(ctx, target.source)
        label = ev.add(f"tree:/{args.path.strip('/')}", listing)
        return ToolOutput(f"[{label}] tree:/{args.path.strip('/')} @ {ref}\n{listing}", tuple(ev.labels))

    async def read_file(args: FileInput, ctx: RunContext) -> ToolOutput:
        target = github_target(args.url)
        piece = await github.read_file(target, args.path, args.start_line, args.end_line)
        ev = _Labels(ctx, target.source)
        parts = [f"{piece.path} (lines {piece.start}-{piece.end} of {piece.total_lines})"]
        for chunk in chunk_lines(piece.numbered.splitlines(), path=piece.path, first_line=piece.start):
            parts += [f"[{ev.add(chunk.locator, chunk.text)}] {chunk.locator}", chunk.text]
        if piece.end < piece.total_lines:
            parts.append(f"... file continues; call again with start_line={piece.end + 1}")
        return ToolOutput("\n".join(parts), tuple(ev.labels))

    async def search_code(args: SearchInput, ctx: RunContext) -> ToolOutput:
        target = github_target(args.url)
        result = await github.search(
            target, args.query, regex=args.regex, path_glob=args.path_glob,
            case_sensitive=args.case_sensitive, max_results=args.max_results,
        )  # fmt: skip
        ev = _Labels(ctx, target.source)
        parts = [f"{result.total_matches} match(es) for {args.query!r} in {result.files_searched} file(s)"
                 + (f"; showing first {len(result.hits)}" if result.total_matches > len(result.hits) else "")]  # fmt: skip
        for hit in result.hits:
            parts += [f"[{ev.add(f'{hit.path}:L{hit.start}-L{hit.end} (match)', hit.snippet)}] {hit.path}:{hit.line}", hit.snippet]
        if not result.hits:
            parts.append("No matches. Try a shorter query, regex=true, or a different path_glob.")
        return ToolOutput("\n".join(parts), tuple(ev.labels))

    async def dependency_graph(args: GraphInput, ctx: RunContext) -> ToolOutput:
        target = github_target(args.url)
        rendered = (await github.dependency_graph(target)).render(scope=args.scope, level=args.level)
        ev = _Labels(ctx, target.source)
        label = ev.add(f"deps:/{args.scope.strip('/')} ({args.level}, static import analysis)", rendered)
        return ToolOutput(f"[{label}] {rendered}", tuple(ev.labels))

    async def code_graph(args: CodeGraphInput, ctx: RunContext) -> ToolOutput:
        target = github_target(args.url)
        graph = await github.code_graph(target)
        ev = _Labels(ctx, target.source)
        if args.op in ("overview", "cycles"):
            if args.op == "overview":
                text = _graph_overview(graph, args.limit)
            else:
                cycles = graph.import_cycles(args.limit)
                text = "Import cycles (files): " + ("\n" + "\n".join("  " + " → ".join(c + c[:1]) for c in cycles) if cycles else "none")
            label = ev.add(f"graph:/ ({args.op}, symbol graph)", text)
            return ToolOutput(f"[{label}] {text}", tuple(ev.labels))
        if not args.symbol:
            raise InvalidInput(f"op={args.op} needs `symbol`")
        if args.op == "find":
            parts = [f"Symbols matching {args.symbol!r}:"]
            for node in graph.find(args.symbol, limit=args.limit):
                row = f"{node.label} — {node.where} (id={node.id})"
                parts.append(f"[{ev.add(f'{node.file}:L{node.line} (definition)', row)}] {row}" if node.file and node.line else f"  {row}")
            if len(parts) == 1:
                parts.append("none; try a shorter name or github_search_code")
            return ToolOutput("\n".join(parts), tuple(ev.labels))
        node, others = _resolve_symbol(graph, args.symbol)
        parts = [f"{node.label} — {node.where} (id={node.id})"]
        if others:
            parts.append("Also matches (pass the id to pick one): " + ", ".join(f"{o.label} ({o.where}, id={o.id})" for o in others))
        if args.op == "neighbors":
            pairs = graph.neighbors(node.id, direction=args.direction, relation=args.relation or None, limit=args.limit)
            edges = [edge for edge, _ in pairs]
            parts.append(f"{len(edges)} relation(s), direction={args.direction}" + (f", relation={args.relation}" if args.relation else "") + ":")
        else:
            if not args.target:
                raise InvalidInput("op=path needs `target`")
            end, _ = _resolve_symbol(graph, args.target)
            found = graph.path(node.id, end.id)
            if found is None:
                parts.append(f"No connection from {node.label} to {end.label} within 8 hops (the graph is undirected; links may be missing for dynamic code).")
                return ToolOutput("\n".join(parts), tuple(ev.labels))
            edges = found
            parts.append(f"Path {node.label} → {end.label} ({len(edges)} hop(s); each hop shows the relation's real direction):")
        for edge in edges:
            text = _edge_text(graph, edge)
            parts.append(f"[{ev.add(_edge_locator(edge), text)}] {text}")
        parts.append(_CONFIDENCE_NOTE)
        return ToolOutput("\n".join(parts), tuple(ev.labels))

    async def coverage(args: RepoInput, ctx: RunContext) -> ToolOutput:
        target = github_target(args.url)
        result = await github.coverage(target, read_ranges(ctx, target.source.canonical_id))
        symbol_graph = await github.built_code_graph(target)
        if symbol_graph is not None:
            importance, measure = symbol_graph.file_importance(), "links"
        else:
            importance, measure = (await github.dependency_graph(target)).fan_in(), "fan-in"
        unread = sorted(
            (p for p in result.source_files if p not in result.read),
            key=lambda p: (-importance.get(p, 0), -result.lines[p]),
        )
        total_lines = sum(result.lines.values()) or 1
        read_lines = sum(min(n, result.lines[p]) for p, n in result.read.items())
        parts = [
            f"Read {len(result.read)}/{len(result.source_files)} source files ({result.read_ratio:.0%}), "
            f"{read_lines}/{total_lines} lines ({read_lines / total_lines:.0%}).",
        ]
        partial = [p for p, n in result.read.items() if n < result.lines[p]]
        if partial:
            parts.append("Partially read: " + ", ".join(f"{p} ({result.read[p]}/{result.lines[p]} lines)" for p in sorted(partial)[:20]))
        if unread:
            parts.append("Unread, most depended-on first:")
            parts += [f"  {p}  ({result.lines[p]} lines, {measure} {importance.get(p, 0)})" for p in unread[:40]]
            if len(unread) > 40:
                parts.append(f"  ... and {len(unread) - 40} more")
        return ToolOutput("\n".join(parts))

    async def fetch_article(args: ArticleInput, ctx: RunContext) -> ToolOutput:
        sources.require(SourceKind.BLOG)
        if parse_github_url(args.url) is not None:
            raise InvalidInput("this is a GitHub repository; use the github_* tools")
        source = blog_source(args.url)
        if source is None:
            raise InvalidInput(f"not an article URL: {args.url}")
        article = await blog.fetch(args.url)
        ev = _Labels(ctx, source)
        parts = [f"# {article.title}", f"Author: {article.author or '-'} | Published: {article.published or '-'}"]
        for section in article.sections:
            heading = section.heading or "Intro"
            parts += ["", f"## {heading}"]
            for chunk in chunk_text_by_chars("\n\n".join(section.paragraphs), locator_prefix=f"§{heading}", max_chars=1_200):
                parts += [f"[{ev.add(chunk.locator, chunk.text)}] {chunk.text}"]
        return ToolOutput("\n".join(parts), tuple(ev.labels))

    return [
        ToolSpec("github_repo_overview", "Read a GitHub repo's metadata, README, config/dependency files and a structure summary. Call this first for any repo.", RepoInput, repo_overview, frozenset({SOURCE}), url_field="url", untrusted_output=True),
        ToolSpec("github_list_tree", "List files/directories of a GitHub repo under a path.", TreeInput, list_tree, frozenset({SOURCE}), url_field="url", untrusted_output=True),
        ToolSpec("github_read_file", "Read a file (or a line range) from a GitHub repo, with line numbers.", FileInput, read_file, frozenset({SOURCE}), url_field="url", untrusted_output=True),
        ToolSpec("github_search_code", "Search the whole repository for text or a regex; returns matching lines with line numbers. Use it to find definitions, call sites and config keys.", SearchInput, search_code, frozenset({SOURCE}), url_field="url", untrusted_output=True),
        ToolSpec("github_dependency_graph", "Deterministic internal import graph (Python/JS/TS/Go): which packages/files import which, fan-in, cycles, external packages.", GraphInput, dependency_graph, frozenset({SOURCE}), url_field="url", untrusted_output=True),
        ToolSpec("github_code_graph", "Symbol-level code graph (classes, functions, methods; calls, inherits, uses, imports) for 25+ languages: core abstractions, communities, who calls what, how A reaches B.", CodeGraphInput, code_graph, frozenset({SOURCE}), url_field="url", untrusted_output=True),
        ToolSpec("github_coverage", "How much of the repository's source code has been read so far, and which important files are still unread.", RepoInput, coverage, frozenset({SOURCE}), url_field="url"),
        ToolSpec("web_fetch_article", "Fetch a blog post / web article and return its readable text by section.", ArticleInput, fetch_article, frozenset({SOURCE}), url_field="url", untrusted_output=True),
    ]  # fmt: skip
