"""github_code_graph: the tool over the CodeGraph port (fake graph), the reader's build cache, and the
graphify adapter on the sample repository (skipped when the `graph` extra is not installed)."""

import asyncio
import glob
import tempfile
import threading

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agent_harness.adapters.models.scripted import ScriptedChatModel, tool_call
from agent_harness.domain.errors import CapabilityUnavailable, ToolFailed
from agent_harness.harness.scripted import TextStep, ToolStep
from agent_harness.ports.code_graph import Community, GraphEdge, GraphNode, Surprise
from agent_harness.sources.github.detector import parse_github_url
from agent_harness.sources.github.repository_reader import RepositoryReader
from builders import chat, client, data_of, make_container
from fakes import REPO_URL, FakeGithubClient, load_repo_fixture

NODES = {
    "store": GraphNode("store", "store.py", "src/tiny_api/store.py", 1),
    "notestore": GraphNode("notestore", "NoteStore", "src/tiny_api/store.py", 1),
    "add": GraphNode("add", "NoteStore.add()", "src/tiny_api/store.py", 5),
    "routes": GraphNode("routes", "routes.py", "src/tiny_api/routes.py", 1),
    "create": GraphNode("create", "create()", "src/tiny_api/routes.py", 10),
    "test_add": GraphNode("test_add", "test_add()", "tests/test_store.py", 4),
}
EDGES = [
    GraphEdge("store", "notestore", "contains", "EXTRACTED", "src/tiny_api/store.py", 1),
    GraphEdge("notestore", "add", "method", "EXTRACTED", "src/tiny_api/store.py", 5),
    GraphEdge("routes", "notestore", "imports", "EXTRACTED", "src/tiny_api/routes.py", 3),
    GraphEdge("create", "add", "calls", "INFERRED", "src/tiny_api/routes.py", 12),
    GraphEdge("test_add", "notestore", "uses", "INFERRED", "tests/test_store.py", 5),
]


class FakeCodeGraph:
    def stats(self):
        return {"nodes": len(NODES), "edges": len(EDGES), "files": 3, "communities": 2}

    def node(self, node_id):
        return NODES.get(node_id)

    def find(self, query, limit=20):
        q = query.lower().removesuffix("()")
        return [n for n in NODES.values() if q in n.label.lower()][:limit]

    def neighbors(self, node_id, *, direction="both", relation=None, limit=50):
        out = []
        for e in EDGES:
            if node_id not in (e.source, e.target) or (relation and e.relation != relation):
                continue
            if (direction == "out" and e.source != node_id) or (direction == "in" and e.target != node_id):
                continue
            out.append((e, NODES[e.target if e.source == node_id else e.source]))
        return out[:limit]

    def path(self, source_id, target_id, max_hops=8):
        return [EDGES[3]] if (source_id, target_id) == ("create", "add") else None

    def hubs(self, limit=10):
        return [(NODES["notestore"], 4)]

    def communities(self, limit=10):
        return [Community(0, 3, (NODES["notestore"], NODES["add"]), ("src/tiny_api/store.py",))]

    def import_cycles(self, limit=10):
        return []

    def surprises(self, limit=5):
        return [Surprise("test_add()", "NoteStore", "uses", "INFERRED", ("tests/test_store.py",), "crosses test/code")]

    def file_importance(self):
        return {"src/tiny_api/routes.py": 9, "src/tiny_api/store.py": 3}


class FakeBuilder:
    def __init__(self, gate: threading.Event | None = None):
        self.calls = 0
        self.gate = gate

    def build(self, files):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        return FakeCodeGraph()


async def test_code_graph_ops_cite_definitions_and_edges():
    steps = [
        ToolStep("github_code_graph", {"url": REPO_URL}),
        ToolStep("github_code_graph", {"url": REPO_URL, "op": "find", "symbol": "add"}),
        ToolStep("github_code_graph", {"url": REPO_URL, "op": "neighbors", "symbol": "NoteStore", "direction": "in"}),
        ToolStep("github_code_graph", {"url": REPO_URL, "op": "path", "symbol": "create", "target": "NoteStore.add"}),
        TextStep("create() gọi NoteStore.add() [E5]."),
    ]
    container = await make_container(script=steps, code_graph_builder=FakeBuilder())
    async with client(container) as http:
        _, events = await chat(http, f"xem {REPO_URL}")
    finished = data_of(events, "tool_finished")
    assert [f["status"] for f in finished] == ["ok"] * 4
    evidence = {e.label: e for _, _, e in container.db.evidence}
    assert evidence["E1"].locator == "graph:/ (overview, symbol graph)"
    assert "NoteStore (src/tiny_api/store.py:L1, degree 4)" in evidence["E1"].quote
    assert evidence["E2"].locator == "src/tiny_api/store.py:L5 (definition)"  # find "add"
    # neighbors direction=in: routes imports it, test_add uses it (contains/method point away or are outgoing)
    assert [evidence[label].locator for label in finished[2]["evidence_labels"]] == [
        "src/tiny_api/store.py:L1 (graph: contains)",
        "src/tiny_api/routes.py:L3 (graph: imports)",
        "tests/test_store.py:L5 (graph: uses)",
    ]
    path_label = finished[3]["evidence_labels"][0]
    assert evidence[path_label].quote == "create() --calls--> NoteStore.add() [INFERRED] at src/tiny_api/routes.py:L12"


async def test_graph_facts_do_not_count_as_reading_and_coverage_ranks_by_graph():
    model = ScriptedChatModel(
        responses=[
            tool_call("github_code_graph", {"url": REPO_URL, "op": "neighbors", "symbol": "NoteStore"}, "a"),
            tool_call("github_coverage", {"url": REPO_URL}, "b"),
            AIMessage("xong [E1]"),
        ]
    )
    container = await make_container(chat_model=model, code_graph_builder=FakeBuilder())
    async with client(container) as http:
        await chat(http, f"xem {REPO_URL}")
    report = [m for m in model.seen[-1] if isinstance(m, ToolMessage)][-1].content
    assert report.startswith("Read 0/5 source files")
    unread = [line.split()[0] for line in report.splitlines() if line.startswith("  ")]
    assert unread[:2] == ["src/tiny_api/routes.py", "src/tiny_api/store.py"]  # code-graph links, not fan-in
    assert "links 9" in report


async def test_code_graph_unavailable_without_builder():
    steps = [ToolStep("github_code_graph", {"url": REPO_URL}), TextStep("không có graph")]
    container = await make_container(script=steps)
    async with client(container) as http:
        _, events = await chat(http, f"xem {REPO_URL}")
    assert data_of(events, "tool_finished")[0]["status"] == "capability_unavailable"


async def test_builtin_skills_expose_the_code_graph():
    container = await make_container(script=[TextStep("ok")])
    async with client(container) as http:
        skills = {s["name"]: s for s in (await http.get("/v1/skills")).json()}
    for name in ("github-architecture", "github-deep-dive"):
        assert "github_code_graph" in skills[name]["allowed_tools"]


# --- reader: one build per snapshot, timeout keeps the build ---------------------------------------


async def test_concurrent_calls_build_once():
    builder = FakeBuilder()
    reader = RepositoryReader(FakeGithubClient(), code_graph_builder=builder)
    target = parse_github_url(REPO_URL)
    graphs = await asyncio.gather(*(reader.code_graph(target) for _ in range(3)))
    assert builder.calls == 1 and len({id(g) for g in graphs}) == 1


async def test_slow_build_times_out_then_is_reused():
    gate = threading.Event()
    builder = FakeBuilder(gate)
    reader = RepositoryReader(FakeGithubClient(), code_graph_builder=builder, code_graph_timeout_s=0.05)
    target = parse_github_url(REPO_URL)
    with pytest.raises(ToolFailed, match="still being built"):
        await reader.code_graph(target)
    assert await reader.built_code_graph(target) is None  # coverage does not wait for it
    gate.set()
    graph = await reader.code_graph(target)
    assert builder.calls == 1 and await reader.built_code_graph(target) is graph


# --- graphify adapter on the real sample repository --------------------------------------------------------


def test_graphify_adapter_on_sample_repository():
    pytest.importorskip("graphify")
    from agent_harness.adapters.graphify.code_graph import GraphifyCodeGraphBuilder

    files = dict(load_repo_fixture()["files"])
    files["../escape.py"] = "x = 1\n"
    before = set(glob.glob(f"{tempfile.gettempdir()}/agent-harness-graph-*"))
    graph = GraphifyCodeGraphBuilder().build(files)
    assert set(glob.glob(f"{tempfile.gettempdir()}/agent-harness-graph-*")) == before  # temp dir removed

    notestore = graph.find("NoteStore")[0]
    assert (notestore.label, notestore.file, notestore.line) == ("NoteStore", "src/tiny_api/store.py", 1)
    assert graph.find("notestore.add")[0].label == "NoteStore.add()"  # methods qualified by their class
    incoming = {(e.relation, other.label, e.confidence) for e, other in graph.neighbors(notestore.id, direction="in")}
    assert ("imports", "routes.py", "EXTRACTED") in incoming
    assert ("uses", "test_add()", "INFERRED") in incoming
    assert not any(n.file and ".." in n.file for n in graph.find("escape"))
    assert graph.file_importance()["src/tiny_api/store.py"] > 0
    assert graph.hubs(3)[0][0].label == "NoteStore"


def test_graphify_adapter_refuses_too_many_files():
    pytest.importorskip("graphify")
    from agent_harness.adapters.graphify.code_graph import GraphifyCodeGraphBuilder

    with pytest.raises(CapabilityUnavailable, match="limited to 2 source files"):
        GraphifyCodeGraphBuilder(max_files=2).build({f"m{i}.py": "x = 1\n" for i in range(3)})
