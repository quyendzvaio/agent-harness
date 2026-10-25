import pytest

from agent_harness.domain.errors import InvalidInput
from agent_harness.research.diagram import Edge, Node, render

NODES = [
    Node("scheduler", "Cloud Scheduler", "scheduler"),
    Node("crawler", 'Crawler "v2" (main)', "service", group="Backend"),
    Node("events", "Event Handler", "queue", group="Backend"),
    Node("db", "Articles database", "database"),
]
EDGES = [
    Edge("10", "events", "db", "Write | upsert", ("E4",), is_async=True),
    Edge("2.1", "crawler", "events", "Get crawling\nsources", ("E2", "E3")),
    Edge("1", "scheduler", "crawler", "Initialize crawler", ("E1",), is_async=True),
    Edge("2.2", "events", "crawler", "Start crawling", ("E3",)),
]
KNOWN = {"E1", "E2", "E3", "E4"}


def test_renders_hand_drawn_flowchart_with_shapes_groups_and_ordered_steps():
    out = render(NODES, EDGES, known_evidence=KNOWN, title="Crawling process")
    code = out.split("```mermaid\n", 1)[1].split("```", 1)[0]
    assert code.startswith("---\nconfig:\n  look: handDrawn\n---\nflowchart LR\n")
    assert 'n1@{ shape: hex, label: "Cloud Scheduler" }' in code
    assert 'n2@{ shape: rounded, label: "Crawler #quot;v2#quot; (main)" }' in code  # quotes escaped
    assert 'subgraph g2["Backend"]' in code and 'n4@{ shape: cyl, label: "Articles database" }' in code
    edges = [line.strip() for line in code.splitlines() if "-->" in line or "-.->" in line]
    assert edges == [
        'n1 -.->|"1. Initialize crawler"| n2',
        'n2 -->|"2.1. Get crawling sources"| n3',
        'n3 -->|"2.2. Start crawling"| n2',
        'n3 -.->|"10. Write #124; upsert"| n4',  # a pipe would end the edge label
    ]
    assert out.startswith("**Crawling process**")
    table = out.split("```", 2)[2]
    assert '| 2.1 | Crawler "v2" (main) → Event Handler | Get crawling sources | [E2] [E3] |' in table
    assert "Write \\| upsert" in table  # pipes escaped in the table


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ([Edge("1", "scheduler", "nowhere", "x", ("E1",))], "unknown node 'nowhere'"),
        ([Edge("1a", "scheduler", "crawler", "x", ("E1",))], "step must look like"),
        ([Edge("1", "scheduler", "crawler", "x", ())], "cite at least one evidence"),
        ([Edge("1", "scheduler", "crawler", "x", ("E99",))], "unknown evidence labels ['E99']"),
    ],
)
def test_invalid_diagrams_explain_what_to_fix(edges, message):
    with pytest.raises(InvalidInput, match=message.replace("[", r"\[").replace("]", r"\]")):
        render(NODES, edges, known_evidence=KNOWN)


def test_duplicate_ids_and_unknown_kinds_are_refused():
    with pytest.raises(InvalidInput, match="duplicate node id 'db'.*kind must be one of"):
        render([*NODES, Node("db", "Other", "mainframe")], EDGES, known_evidence=KNOWN)
