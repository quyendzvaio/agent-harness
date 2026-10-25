import asyncio

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from agent_harness.adapters.models.scripted import ScriptedChatModel, tool_call
from agent_harness.domain.policies import ExecutionLimits
from agent_harness.harness.scripted import TextStep, ToolStep
from agent_harness.ports.claim_verifier import ClaimVerdict
from builders import chat, client, data_of, kinds, make_container, make_settings
from fakes import REPO_URL


async def test_search_dependency_graph_and_coverage_tools():
    steps = [
        ToolStep("github_dependency_graph", {"url": REPO_URL, "level": "file"}),
        ToolStep("github_search_code", {"url": REPO_URL, "query": "NoteStore", "path_glob": "src/*"}),
        ToolStep("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/store.py"}),
        ToolStep("github_coverage", {"url": REPO_URL}),
        TextStep("routes.py dùng NoteStore [E1] [E2]."),
    ]
    container = await make_container(script=steps)
    async with client(container) as http:
        _, events = await chat(http, f"xem {REPO_URL}")
    finished = data_of(events, "tool_finished")
    assert [f["status"] for f in finished] == ["ok"] * 4
    assert finished[0]["evidence_labels"] == ["E1"]
    assert len(finished[1]["evidence_labels"]) == 3  # 3 matches under src/
    evidence = {e.label: e for _, _, e in container.db.evidence}
    assert "src/tiny_api/routes.py → src/tiny_api/store.py" in evidence["E1"].quote
    assert evidence["E2"].locator == "src/tiny_api/routes.py:L2-L4 (match)"
    invocations = {i.tool_name: i for i in container.db.tool_invocations}
    assert invocations["github_coverage"].status == "ok"


async def test_coverage_reflects_files_read_and_ranks_unread_by_fan_in():
    model = ScriptedChatModel(
        responses=[
            tool_call("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/main.py"}, "a"),
            tool_call("github_coverage", {"url": REPO_URL}, "b"),
            AIMessage("xong [E1]"),
        ]
    )
    container = await make_container(chat_model=model)
    async with client(container) as http:
        await chat(http, f"xem {REPO_URL}")
    report = [m for m in model.seen[-1] if isinstance(m, ToolMessage)][-1].content
    assert report.startswith("Read 1/5 source files (20%)")
    unread = [line.strip().split()[0] for line in report.splitlines() if line.startswith("  ")]
    assert unread[0] == "src/tiny_api/store.py"  # highest fan-in first


async def test_deep_dive_skill_raises_budget_within_ceiling():
    settings = make_settings(
        limits=ExecutionLimits(max_model_calls=3, max_tool_calls=2, turn_timeout_s=30, retry_backoff_s=0),
        limits_ceiling=ExecutionLimits(max_model_calls=10, max_tool_calls=100, turn_timeout_s=60),
    )
    steps = [ToolStep("load_skill", {"name": "github-deep-dive"})] + [ToolStep("github_list_tree", {"url": REPO_URL})] * 4 + [TextStep("ok [E1]")]  # fmt: skip
    container = await make_container(settings, script=steps)
    async with client(container) as http:
        _, events = await chat(http, f"đọc toàn bộ {REPO_URL}")
    # 5 tool calls and 6 model steps would break the base budget (2 / 3); the skill raised it.
    assert kinds(events)[-1] == "done"
    assert data_of(events, "skill_loaded")[0]["limits"] == {"max_model_calls": 10, "max_tool_calls": 100, "turn_timeout_s": 60}


async def test_skill_budget_extends_the_turn_deadline():
    settings = make_settings(
        limits=ExecutionLimits(turn_timeout_s=0.2, retry_backoff_s=0),
        limits_ceiling=ExecutionLimits(turn_timeout_s=5),
    )

    class SlowAfterSkill:
        def __init__(self, executor):
            self._executor = executor

        async def run(self, ctx):
            from agent_harness.harness.tools.spec import ToolCall

            await self._executor.execute(ToolCall("load_skill", {"name": "github-deep-dive"}), ctx)
            await asyncio.sleep(0.4)  # past the base timeout, inside the raised one
            return "ok"

    container = await make_container(settings, engine_factory=SlowAfterSkill)
    async with client(container) as http:
        _, events = await chat(http, "đọc toàn bộ")
    assert kinds(events)[-1] == "done"


async def test_without_skill_the_base_timeout_applies():
    settings = make_settings(limits=ExecutionLimits(turn_timeout_s=0.2, retry_backoff_s=0))

    class Slow:
        def __init__(self, executor):
            pass

        async def run(self, ctx):
            await asyncio.sleep(1)
            return "never"

    container = await make_container(settings, engine_factory=Slow)
    async with client(container) as http:
        _, events = await chat(http, "hi")
    assert data_of(events, "error")[0]["message"] == "turn_timeout_s=0.2 reached"


async def test_notes_survive_in_the_system_prompt():
    model = ScriptedChatModel(
        responses=[
            tool_call("save_note", {"topic": "src/tiny_api", "text": "NoteStore giữ ghi chú trong list [E1]"}, "a"),
            AIMessage("xong"),
        ]
    )
    container = await make_container(chat_model=model)
    async with client(container) as http:
        await chat(http, "đọc")
    system = model.seen[-1][0]
    assert isinstance(system, SystemMessage)
    assert "# Ghi chú đã lưu" in system.content and "NoteStore giữ ghi chú trong list [E1]" in system.content


class RecordingVerifier:
    def __init__(self, fail: bool = False) -> None:
        self.claims = []
        self.fail = fail

    async def verify(self, claims):
        if self.fail:
            raise RuntimeError("classifier down")
        self.claims = claims
        return [
            ClaimVerdict(c, "supported" if "FastAPI" in c.sentence else "unsupported", 0.9 if "FastAPI" in c.sentence else 0.1)
            for c in claims
        ]


async def test_claim_verifier_receives_sentences_with_their_evidence():
    verifier = RecordingVerifier()
    answer = (
        "Repo dùng FastAPI làm web framework [E1].\n"
        "Repo lưu dữ liệu vào PostgreSQL [E1] [E9].\n"
        "## Nhận định của agent\nKiến trúc đơn giản [E1]."
    )
    steps = [ToolStep("github_list_tree", {"url": REPO_URL}), TextStep(answer)]
    container = await make_container(script=steps, claim_verifier=verifier)
    async with client(container) as http:
        _, events = await chat(http, f"xem {REPO_URL}")
    assert [c.sentence for c in verifier.claims] == ["Repo dùng FastAPI làm web framework.", "Repo lưu dữ liệu vào PostgreSQL."]
    assert [e.label for e in verifier.claims[1].evidence] == ["E1"]  # E9 does not exist; inference section skipped
    claims = data_of(events, "citation_report")[0]["claims"]
    assert claims["checked"] == 2 and claims["supported"] == 1 and claims["unsupported"] == 1
    assert claims["flagged"][0]["probability"] == 0.1


async def test_failing_verifier_never_fails_the_turn():
    steps = [ToolStep("github_list_tree", {"url": REPO_URL}), TextStep("Repo dùng FastAPI làm framework web [E1].")]
    container = await make_container(script=steps, claim_verifier=RecordingVerifier(fail=True))
    async with client(container) as http:
        _, events = await chat(http, f"xem {REPO_URL}")
    assert kinds(events)[-1] == "done" and "claims" not in data_of(events, "citation_report")[0]
