from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from agent_harness.capabilities.system_map_tools import SystemMaps, citer
from agent_harness.domain.errors import CapabilityUnavailable, InvalidInput
from agent_harness.domain.events import EventType
from agent_harness.domain.evidence import Evidence
from agent_harness.domain.sources import SourceKind
from agent_harness.harness.context import RunContext
from agent_harness.harness.tools.spec import DIAGRAM, ToolOutput, ToolSpec
from agent_harness.research.diagram import KINDS, Edge, Node, render
from agent_harness.sources.github.detector import parse_github_url
from agent_harness.sources.github.system_map import SystemMap
from agent_harness.sources.github.system_map.render import diagram_plan

NodeKind = Literal[KINDS]  # type: ignore[valid-type]

#: Evidence that shows code: a file read or a search hit (`path:Lx-Ly`), or a system-map channel.
_CODE_LOCATOR = re.compile(r"^(?P<path>.+):L\d+-L\d+(?: \(match\))?$")
_DOC_PATH = re.compile(r"\.(md|mdx|rst|txt|adoc)$", re.I)
_INFERRED_NOTE = "`(?)` = INFERRED: khớp hoặc địa chỉ suy từ mặc định/cổng publish/tên, chưa có bằng chứng hai đầu; đọc file:dòng trong bảng trước khi khẳng định."


class DiagramNode(BaseModel):
    id: str = Field(min_length=1, max_length=40, description="Short unique id, e.g. 'api', 'kafka'")
    label: str = Field(min_length=1, max_length=60, description="Name shown in the box, e.g. 'Crawler Service'")
    kind: NodeKind = Field("service", description="client | service | worker | scheduler | queue | database | cache | storage | external")  # fmt: skip
    group: str | None = Field(None, max_length=40, description="Optional boundary drawn around nodes, e.g. 'Backend'")


class DiagramEdge(BaseModel):
    step: str = Field(description="Execution order: '1', '2.1', '2.2', '3' ... (sub-steps share a parent number)")
    source: str = Field(description="Node id where the flow starts")
    target: str = Field(description="Node id the flow goes to")
    label: str = Field(min_length=1, max_length=60, description="What happens, e.g. 'Get crawling sources'")
    evidence: list[str] = Field(min_length=1, description="Evidence labels showing this flow in the code, e.g. ['E12']")
    is_async: bool = Field(False, description="True for asynchronous flows (events, queues, background writes): dashed")


class DiagramInput(BaseModel):
    source: Literal["system_map", "manual"] | None = Field(
        None,
        description="system_map = the harness draws every node and edge from github_system_map (give `url`, no "
        "nodes/edges); manual = you give nodes/edges, each edge citing code you read. Default: system_map when "
        "`url` is given without nodes, else manual",
    )
    url: str = Field("", description="system_map: the GitHub repository URL")
    variant: str = Field("", max_length=80, description="system_map: only this compose variant/profile, e.g. 'full'")
    process: str = Field("", max_length=120, description="system_map: only edges touching this node (a flow diagram)")
    title: str = Field("", max_length=80)
    direction: Literal["LR", "TB"] = "LR"
    nodes: list[DiagramNode] = Field(default_factory=list, max_length=30)
    edges: list[DiagramEdge] = Field(default_factory=list, max_length=50)


def build_diagram_tools(maps: SystemMaps | None = None) -> list[ToolSpec]:
    async def architecture_diagram(args: DiagramInput, ctx: RunContext) -> ToolOutput:
        mode = args.source or ("system_map" if args.url and not args.nodes else "manual")
        if mode == "system_map":
            markdown, counts = await _from_map(args, ctx, maps)
        else:
            if not args.nodes or not args.edges:
                raise InvalidInput("source=manual needs nodes and edges; or pass source=system_map with the repository url")
            await _check_manual(args, ctx, maps)
            markdown = render(
                [Node(n.id, n.label, n.kind, n.group) for n in args.nodes],
                [Edge(e.step.strip(), e.source, e.target, e.label, tuple(_label(x) for x in e.evidence), e.is_async) for e in args.edges],
                known_evidence=ctx.evidence.labels(),
                direction=args.direction,
                title=args.title,
            )  # fmt: skip
            counts = {"nodes": len(args.nodes), "edges": len(args.edges)}
        ctx.diagrams.append(markdown)
        # Streamed now, at the start of the answer: the user sees it even if the model is slow to write the rest
        # (or the turn runs out of time), and the model has no reason to re-type it.
        heading = "## Sơ đồ kiến trúc" if ctx.intent.language == "vi" else "## Architecture diagram"
        block = f"{heading}\n\n{markdown}"
        ctx.answer_prefix.append(block)
        ctx.emit(EventType.TOKEN, text=block + "\n\n", message_id=f"diagram-{len(ctx.diagrams)}")
        return ToolOutput(
            "Diagram drawn and ALREADY INSERTED into the answer shown to the user (mermaid code + step table below, for "
            "reference). Do NOT copy it and do NOT repeat earlier answers or tables: write only a short explanation of "
            "the numbered steps, citing [E#].\n\n" + markdown,
            data={"source": mode, **counts},
        )

    return [
        ToolSpec(
            "architecture_diagram",
            "Draw an architecture/flow diagram (hand-drawn Mermaid) of runtime components and numbered flows. Prefer "
            "source=system_map (with the repo url): nodes and edges come from github_system_map, INFERRED edges are "
            "marked (?). source=manual only when there is no map: every edge must cite code you read (github_read_file "
            "or github_search_code labels), never README or a guess. Only available when the user asked for a diagram.",
            DiagramInput,
            architecture_diagram,
            frozenset({DIAGRAM}),
        )
    ]


async def _from_map(args: DiagramInput, ctx: RunContext, maps: SystemMaps | None) -> tuple[str, dict[str, int]]:
    if maps is None:
        raise InvalidInput("source=system_map is not available on this server; use source=manual")
    if not args.url:
        raise InvalidInput("source=system_map needs `url` (the GitHub repository)")
    target, smap = await maps.get(args.url)
    labels: list[str] = []
    nodes, edges = diagram_plan(smap, citer(ctx, target, labels), variant=args.variant or None, process=args.process or None)
    title = args.title or f"Kiến trúc khai báo — {target.owner}/{target.repo}" + (f" ({args.variant})" if args.variant else "")
    markdown = render(
        [Node(n.id, n.label, n.kind, n.group) for n in nodes],
        [Edge(e.step, e.source, e.target, e.label, tuple(x.strip("[]") for x in e.evidence), e.is_async) for e in edges],
        known_evidence=ctx.evidence.labels(),
        direction=args.direction,
        title=title,
    )  # fmt: skip
    if any(e.inferred for e in edges):
        markdown += "\n\n" + _INFERRED_NOTE
    return markdown, {"nodes": len(nodes), "edges": len(edges), "inferred": sum(e.inferred for e in edges)}


async def _check_manual(args: DiagramInput, ctx: RunContext, maps: SystemMaps | None) -> None:
    """Each edge cites code (a read, a search hit or a map channel); between two nodes of the map, an edge the map
    does not have needs code of both ends (two such labels)."""
    problems: list[str] = []
    cited: dict[str, list[Evidence]] = {}
    for edge in args.edges:
        found = [e for x in edge.evidence if (e := ctx.evidence.get(_label(x))) is not None]
        cited[edge.step] = [e for e in found if _shows_code(e)]
        if found and not cited[edge.step]:
            problems.append(f"edge {edge.step} ({edge.source} -> {edge.target}): its evidence is README/tree/graph/manifest "
                            "rows, not code; cite a github_read_file or github_search_code label of the call site")  # fmt: skip
    smap = await _map_of(cited, maps)
    if smap is not None:
        names = {n.id: _process_of(smap, n) for n in args.nodes}
        linked = {frozenset((c.src, c.dst)) for c in smap.channels}
        for edge in args.edges:
            a, b = names.get(edge.source), names.get(edge.target)
            if a is None or b is None or a == b or frozenset((a, b)) in linked:
                continue
            if len(cited.get(edge.step, [])) < 2:
                problems.append(f"edge {edge.step} ({a} -> {b}): the system map has no channel between them; cite the code "
                                "of both ends (client call and server route/consumer) or drop the edge")  # fmt: skip
    if problems:
        raise InvalidInput("; ".join(problems))


def _shows_code(item: Evidence) -> bool:
    if item.source.kind != SourceKind.GITHUB:
        return True
    if "(channel:" in item.locator:
        return True
    match = _CODE_LOCATOR.match(item.locator)
    return bool(match) and not _DOC_PATH.search(match["path"])


async def _map_of(cited: dict[str, list[Evidence]], maps: SystemMaps | None) -> SystemMap | None:
    """The map of the repository the edges cite, if it can be built; never fails the diagram."""
    if maps is None:
        return None
    urls = {e.source.url for items in cited.values() for e in items if e.source.kind == SourceKind.GITHUB}
    if len(urls) != 1:
        return None
    target = parse_github_url(urls.pop())
    if target is None:
        return None
    try:
        return await maps.github.system_map(target)
    except CapabilityUnavailable:
        return None


def _process_of(smap: SystemMap, node: DiagramNode) -> str | None:
    for candidate in (node.id, node.label):
        key = _norm(candidate)
        for process in smap.processes.values():
            if process.is_boundary:
                continue
            if key in {_norm(process.name), *(_norm(a) for a in process.aliases)}:
                return process.name
    return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _label(value: str) -> str:
    return value.strip().strip("[]").strip()
