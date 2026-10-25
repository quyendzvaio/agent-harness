"""github_system_map through the harness: evidence that is not a read, section filters, diagrams drawn from
the map, manual diagrams held to the map, the new skill and the context-clearing exclusions."""

from langchain_core.messages import AIMessage, ToolMessage

from agent_harness.adapters.langchain.engine import _KEEP_TOOLS
from agent_harness.adapters.models.scripted import ScriptedChatModel, tool_call
from agent_harness.harness.scripted import TextStep, ToolStep
from builders import chat, client, data_of, make_container
from fakes import FakeGithubClient
from fixtures.system_map import compose_repo, docs_repo

URL = compose_repo.URL


def tool_outputs(model: ScriptedChatModel) -> list[str]:
    return [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)]


async def test_map_rows_are_evidence_but_never_count_as_having_read_a_file():
    model = ScriptedChatModel(
        responses=[
            tool_call("github_system_map", {"url": URL}, "a"),
            tool_call("github_coverage", {"url": URL}, "b"),
            AIMessage("redis được khai báo nhưng không ai dùng [E1]."),
        ]
    )
    container = await make_container(chat_model=model, github=FakeGithubClient(compose_repo.REPO))
    async with client(container) as http:
        _, events = await chat(http, f"kiến trúc hệ thống {URL}")
    finished = data_of(events, "tool_finished")
    assert [f["status"] for f in finished] == ["ok", "ok"]
    overview, coverage = tool_outputs(model)
    assert "Kiến trúc ĐƯỢC KHAI BÁO từ docker-compose.yml, docker-compose.full.yml. Chưa đối chiếu với runtime." in overview
    assert "## 4. Khoảng trống (Q9)" in overview and "[E1]" in overview
    assert "Read 0/" in coverage  # map rows cite `path:Lx (node)` locators, which are not file reads
    locators = [e.locator for _, _, e in container.db.evidence]
    assert locators and all(loc.endswith(")") for loc in locators)
    assert data_of(events, "citation_report")[0]["invalid"] == []


async def test_section_variant_and_process_filters_reach_the_renderer():
    model = ScriptedChatModel(
        responses=[
            tool_call("github_system_map", {"url": URL, "section": "channels", "variant": "full", "process": "worker"}, "a"),
            tool_call("github_system_map", {"url": URL, "section": "gaps", "variant": "nope"}, "b"),
            AIMessage("xong [E1]"),
        ]
    )
    container = await make_container(chat_model=model, github=FakeGithubClient(compose_repo.REPO))
    async with client(container) as http:
        _, events = await chat(http, f"kiến trúc hệ thống {URL}")
    assert [f["status"] for f in data_of(events, "tool_finished")] == ["ok", "invalid_input"]
    channels = tool_outputs(model)[0]
    assert "worker → api" in channels and "kafka → worker" in channels and "frontend → api" not in channels
    assert "unknown variant 'nope'" in container.db.tool_invocations[1].error["message"]


async def test_repo_without_runtime_manifests_says_so():
    model = ScriptedChatModel(responses=[tool_call("github_system_map", {"url": docs_repo.URL}, "a"), AIMessage("ok [E1]")])
    container = await make_container(chat_model=model, github=FakeGithubClient(docs_repo.REPO))
    async with client(container) as http:
        await chat(http, f"service nào chạy trong {docs_repo.URL}")
    text = tool_outputs(model)[0]
    assert "Không có topology runtime trong repo" in text and "dev-host, external-hosted" in text
    assert "vitepress dev docs" in text


async def test_diagram_is_drawn_from_the_map_with_inferred_edges_marked():
    steps = [
        ToolStep("architecture_diagram", {"source": "system_map", "url": URL, "variant": "full"}),
        TextStep("Sơ đồ bên dưới [E1]."),
    ]
    container = await make_container(script=steps, github=FakeGithubClient(compose_repo.REPO))
    async with client(container) as http:
        _, events = await chat(http, f"Vẽ sơ đồ kiến trúc {URL}")
    assert [f["status"] for f in data_of(events, "tool_finished")] == ["ok"]
    answer = container.db.messages[-1].content
    assert "look: handDrawn" in answer and "Kiến trúc khai báo — acme/mini-soc (full)" in answer
    assert 'queue: publish security_events' in answer and "-.->" in answer  # the queue edge is dashed
    assert "(?)" in answer and "`(?)` = INFERRED" in answer  # the browser edge resolves through localhost:8000
    assert "| 1. | user → api |" not in answer and "| 1 | user → api |" in answer
    assert data_of(events, "citation_report")[0]["invalid"] == []


async def test_manual_diagram_edges_need_code_of_both_ends_or_a_channel():
    nodes = [
        {"id": "frontend", "label": "frontend", "kind": "service"},
        {"id": "api", "label": "api", "kind": "service"},
        {"id": "kafka", "label": "kafka", "kind": "queue"},
    ]

    def script(ctx):
        yield ToolStep("github_read_file", {"url": URL, "path": "frontend/lib/api.js"})  # E1: code
        yield ToolStep("github_system_map", {"url": URL})
        node_label = next(e.label for e in ctx.evidence.items() if e.locator.endswith("(node)"))
        yield ToolStep("architecture_diagram", {  # a node row is a manifest fact, not the code of a flow
            "source": "manual", "nodes": nodes,
            "edges": [{"step": "1", "source": "frontend", "target": "api", "label": "GET /public", "evidence": [node_label]}],
        })  # fmt: skip
        yield ToolStep("architecture_diagram", {  # the README claim: no channel in the map, only one end read
            "source": "manual", "nodes": nodes,
            "edges": [{"step": "1", "source": "frontend", "target": "kafka", "label": "stream", "evidence": ["E1"]}],
        })  # fmt: skip
        yield ToolStep("architecture_diagram", {  # the map has frontend -> api; the read of the call site is enough
            "source": "manual", "nodes": nodes,
            "edges": [{"step": "1", "source": "frontend", "target": "api", "label": "GET /public", "evidence": ["E1"]}],
        })  # fmt: skip
        yield TextStep("Sơ đồ [E1].")

    container = await make_container(script=script, github=FakeGithubClient(compose_repo.REPO))
    async with client(container) as http:
        _, events = await chat(http, f"Vẽ sơ đồ kiến trúc {URL}")
    statuses = [f["status"] for f in data_of(events, "tool_finished")]
    assert statuses == ["ok", "ok", "invalid_input", "invalid_input", "ok"]
    errors = [i.error["message"] for i in container.db.tool_invocations if i.error]
    assert "not code" in errors[0] and "frontend -> api" in errors[0]
    assert "no channel between them" in errors[1] and "frontend -> kafka" in errors[1]
    assert container.db.messages[-1].content.count("```mermaid") == 1


async def test_the_skill_is_listed_with_the_map_tool_and_the_tool_survives_context_clearing():
    container = await make_container(script=[TextStep("x")])
    async with client(container) as http:
        detail = (await http.get("/v1/skills/github-system-architecture")).json()
    assert detail["allowed_tools"][0] == "github_system_map" and "5 phần" in detail["instructions"]
    assert detail["limits"]["max_tool_calls"] == 40
    assert "github_system_map" in _KEEP_TOOLS


async def test_the_skill_gets_the_tables_streamed_into_the_answer_and_writes_only_its_verification():
    steps = [
        ToolStep("load_skill", {"name": "github-system-architecture"}),
        ToolStep("github_system_map", {"url": URL}),
        ToolStep("github_read_file", {"url": URL, "path": "services/worker/worker.py", "start_line": 1, "end_line": 30}),
        TextStep("## Kiểm chứng\n- worker → api: xác nhận EXTRACTED [E1].\n\n## Nhận định của agent\nỔn."),
    ]
    container = await make_container(script=steps, github=FakeGithubClient(compose_repo.REPO))
    async with client(container) as http:
        _, events = await chat(http, f"kiến trúc hệ thống {URL}")
    tokens = data_of(events, "token")
    assert tokens[0]["message_id"] == "system_map"  # streamed before the model's own text
    assert tokens[0]["text"].startswith("Kiến trúc ĐƯỢC KHAI BÁO từ docker-compose.yml")
    assert "## Đọc để kiểm chứng" not in tokens[0]["text"]  # what to read next is for the model only
    answer = container.db.messages[-1].content
    assert answer.startswith("Kiến trúc ĐƯỢC KHAI BÁO") and answer.count("## 2. Cạnh") == 1
    assert answer.index("## 4. Khoảng trống") < answer.index("## Kiểm chứng") < answer.index("## Nhận định")
    assert answer == "".join(t["text"] for t in tokens)  # a reload shows what was streamed
    assert data_of(events, "citation_report")[0]["invalid"] == []
    invocation = next(i for i in container.db.tool_invocations if i.tool_name == "github_system_map")
    assert invocation.status == "ok"


async def test_without_the_skill_the_map_stays_a_tool_result():
    steps = [ToolStep("github_system_map", {"url": URL}), TextStep("Tóm tắt [E1].")]
    container = await make_container(script=steps, github=FakeGithubClient(compose_repo.REPO))
    async with client(container) as http:
        _, events = await chat(http, f"kiến trúc hệ thống {URL}")
    assert container.db.messages[-1].content == "Tóm tắt [E1]."
    assert not [t for t in data_of(events, "token") if t.get("message_id") == "system_map"]
