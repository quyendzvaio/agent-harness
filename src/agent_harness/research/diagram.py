"""Architecture diagrams as hand-drawn Mermaid flowcharts, built from a structured description.

The model describes runtime components and numbered flows; this module writes the Mermaid code, so
the output always parses (labels escaped, ids generated) and every flow carries its evidence labels.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from agent_harness.domain.errors import InvalidInput

#: Mermaid ≥ 11.3 shape names (`id@{ shape: ... }`).
SHAPES = {
    "client": "stadium",
    "service": "rounded",
    "worker": "fr-rect",
    "scheduler": "hex",
    "queue": "h-cyl",
    "database": "cyl",
    "cache": "lin-cyl",
    "storage": "lin-cyl",
    "external": "notch-rect",
}
KINDS = tuple(SHAPES)
_STEP = re.compile(r"^\d+(\.\d+)*$")
MAX_NODES = 30
MAX_EDGES = 50
MAX_LABEL = 60


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    label: str
    kind: str = "service"
    group: str | None = None


@dataclass(frozen=True, slots=True)
class Edge:
    step: str
    source: str
    target: str
    label: str
    evidence: tuple[str, ...]
    is_async: bool = False


def render(
    nodes: Sequence[Node],
    edges: Sequence[Edge],
    *,
    known_evidence: Iterable[str],
    direction: str = "LR",
    title: str = "",
) -> str:
    """Markdown: a ```mermaid block and a table of the numbered steps with their citations."""
    _validate(nodes, edges, set(known_evidence))
    ids = {node.id: f"n{i}" for i, node in enumerate(nodes, 1)}
    ordered = sorted(edges, key=lambda e: tuple(int(p) for p in e.step.split(".")))

    lines = ["---", "config:", "  look: handDrawn", "---", f"flowchart {direction}"]
    grouped: dict[str | None, list[Node]] = {}
    for node in nodes:
        grouped.setdefault(node.group, []).append(node)
    for g, (group, members) in enumerate(grouped.items(), 1):
        indent = "  "
        if group:
            lines.append(f'  subgraph g{g}["{_escape(group)}"]')
            indent = "    "
        lines += [f'{indent}{ids[n.id]}@{{ shape: {SHAPES[n.kind]}, label: "{_escape(n.label)}" }}' for n in members]
        if group:
            lines.append("  end")
    for edge in ordered:
        arrow = "-.->" if edge.is_async else "-->"
        lines.append(f'  {ids[edge.source]} {arrow}|"{edge.step}. {_escape(edge.label)}"| {ids[edge.target]}')
    # No classDef colours: the hand-drawn look hatches fills with the stroke colour and buries the labels;
    # the shape alone tells the kind apart.

    labels = {node.id: node.label for node in nodes}
    table = ["| Bước | Luồng | Mô tả | Nguồn |", "|---|---|---|---|"]
    for edge in ordered:
        flow = f"{_cell(labels[edge.source])} → {_cell(labels[edge.target])}"
        table.append(f"| {edge.step} | {flow} | {_cell(edge.label)} | {' '.join(f'[{e}]' for e in edge.evidence)} |")

    heading = f"**{_cell(title)}**\n\n" if title else ""
    return heading + "```mermaid\n" + "\n".join(lines) + "\n```\n\n" + "\n".join(table)


def _validate(nodes: Sequence[Node], edges: Sequence[Edge], known: set[str]) -> None:
    problems: list[str] = []
    if not nodes or not edges:
        problems.append("a diagram needs at least one node and one edge")
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        problems.append(f"at most {MAX_NODES} nodes and {MAX_EDGES} edges; split it into several diagrams")
    seen: set[str] = set()
    for node in nodes:
        if node.id in seen:
            problems.append(f"duplicate node id {node.id!r}")
        seen.add(node.id)
        if node.kind not in SHAPES:
            problems.append(f"node {node.id!r}: kind must be one of {', '.join(KINDS)}")
        if not node.label.strip() or len(node.label) > MAX_LABEL:
            problems.append(f"node {node.id!r}: label must be 1-{MAX_LABEL} characters")
    unknown_labels: set[str] = set()
    for edge in edges:
        where = f"edge {edge.step} ({edge.source} -> {edge.target})"
        if not _STEP.match(edge.step):
            problems.append(f"{where}: step must look like 1, 2.1 or 3.2.1")
        for end in (edge.source, edge.target):
            if end not in seen:
                problems.append(f"{where}: unknown node {end!r}")
        if not edge.label.strip() or len(edge.label) > MAX_LABEL:
            problems.append(f"{where}: label must be 1-{MAX_LABEL} characters")
        if not edge.evidence:
            problems.append(f"{where}: cite at least one evidence label [E#] that shows this flow in the code")
        unknown_labels |= set(edge.evidence) - known
    if unknown_labels:
        problems.append(f"unknown evidence labels {sorted(unknown_labels)}; use labels returned by the source tools")
    if problems:
        raise InvalidInput("; ".join(problems))


def _escape(text: str) -> str:
    return " ".join(text.split()).replace('"', "#quot;").replace("|", "#124;")


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")
