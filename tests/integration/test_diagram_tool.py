"""architecture_diagram: only when asked, evidence-checked, always reaches the answer; and the output guard
retrying a model call that leaked its reasoning (real ChatOpenAI over a mocked OpenAI-compatible endpoint)."""

import json

import httpx2
from langchain_core.messages import AIMessage

from agent_harness.adapters.models.openai_compatible import GuardedChatOpenAI
from agent_harness.adapters.models.scripted import ScriptedChatModel, tool_call
from agent_harness.harness.scripted import TextStep, ToolStep
from builders import chat, client, data_of, kinds, make_container, make_settings
from fakes import REPO_URL

DIAGRAM = {
    "title": "Luồng ghi chú",
    "nodes": [
        {"id": "client", "label": "HTTP client", "kind": "client"},
        {"id": "api", "label": "Routes", "kind": "service"},
        {"id": "store", "label": "NoteStore", "kind": "storage"},
    ],
    "edges": [
        {"step": "1", "source": "client", "target": "api", "label": "POST /notes", "evidence": ["[E1]"]},
        {"step": "2", "source": "api", "target": "store", "label": "add(note)", "evidence": ["E1"]},
    ],
}
ASK = f"Vẽ sơ đồ kiến trúc {REPO_URL}"


async def test_diagram_tool_is_hidden_unless_the_user_asks_for_a_diagram():
    for message, visible in ((f"phân tích kiến trúc {REPO_URL}", False), (ASK, True)):
        model = ScriptedChatModel(responses=[AIMessage("ok")])
        container = await make_container(chat_model=model)
        async with client(container) as http:
            await chat(http, message)
        assert ("architecture_diagram" in model.seen_tools[0]) is visible

    steps = [ToolStep("architecture_diagram", DIAGRAM), TextStep("không vẽ")]
    container = await make_container(script=steps)
    async with client(container) as http:
        _, events = await chat(http, f"phân tích {REPO_URL}")
    assert data_of(events, "tool_finished")[0]["status"] == "policy_denied"  # called anyway: blocked


async def test_diagram_is_streamed_into_the_answer_as_soon_as_it_is_drawn():
    steps = [
        ToolStep("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/routes.py"}),
        ToolStep("architecture_diagram", DIAGRAM),
        TextStep("Routes ghi vào NoteStore [E1]."),
    ]
    container = await make_container(script=steps)
    async with client(container) as http:
        _, events = await chat(http, ASK)
    assert [f["status"] for f in data_of(events, "tool_finished")] == ["ok", "ok"]
    answer = container.db.messages[-1].content
    assert answer.startswith("## Sơ đồ kiến trúc\n\n**Luồng ghi chú**") and answer.endswith("Routes ghi vào NoteStore [E1].")
    assert "look: handDrawn" in answer and 'n2 -->|"2. add(note)"| n3' in answer
    tokens = data_of(events, "token")
    assert tokens[0]["message_id"] == "diagram-1" and "```mermaid" in tokens[0]["text"]  # before the model's text
    assert answer == "".join(t["text"] for t in tokens)  # a reload shows what was streamed
    assert data_of(events, "citation_report")[0]["invalid"] == []


async def test_diagram_copied_by_the_model_is_not_appended_twice():
    def answer_with_copy(ctx):
        yield ToolStep("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/routes.py"})
        yield ToolStep("architecture_diagram", DIAGRAM)
        yield TextStep("Sơ đồ:\n\n" + ctx.diagrams[0])

    container = await make_container(script=answer_with_copy)
    async with client(container) as http:
        await chat(http, ASK)
    assert container.db.messages[-1].content.count("```mermaid") == 1


async def test_unknown_evidence_is_rejected_so_the_model_can_fix_it():
    steps = [ToolStep("architecture_diagram", DIAGRAM), TextStep("chưa có evidence")]
    container = await make_container(script=steps)
    async with client(container) as http:
        _, events = await chat(http, ASK)
    assert data_of(events, "tool_finished")[0]["status"] == "invalid_input"
    assert container.db.tool_invocations[0].error["message"].startswith("unknown evidence labels ['E1']")


async def test_diagram_via_langchain_engine():
    model = ScriptedChatModel(
        responses=[
            tool_call("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/routes.py"}, "a"),
            tool_call("architecture_diagram", DIAGRAM, "b"),
            AIMessage("Xem sơ đồ dưới [E1]."),
        ]
    )
    container = await make_container(chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, ASK)
    assert kinds(events)[-1] == "done"
    assert "```mermaid" in container.db.messages[-1].content
    # Nested tool arguments reach the harness as plain JSON (they are persisted as JSONB).
    drawn = next(i for i in container.db.tool_invocations if i.tool_name == "architecture_diagram")
    assert drawn.status == "ok" and json.loads(json.dumps(drawn.input))["nodes"][0]["id"] == "client"


# --- output guard over a mocked OpenAI-compatible endpoint ------------------------------------------------------

LEAK = (
    "We have a lot of code. We need to produce a summary of the repository. The user just gave the link again. "
    "We should include key modules: core, routers, services. Also mention Docker Compose and the tests folder."
)


def _sse(text: str) -> bytes:
    chunks = [text[i : i + 40] for i in range(0, len(text), 40)]
    events = [
        {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
         "choices": [{"index": 0, "delta": {"role": "assistant", "content": piece}, "finish_reason": None}]}
        for piece in chunks
    ]  # fmt: skip
    events.append({"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})  # fmt: skip
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode() + b"data: [DONE]\n\n"


async def test_leaked_reasoning_is_retried_and_never_reaches_the_answer():
    replies = iter([LEAK, "Câu trả lời sạch [E1]."])
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(json.loads(request.content)["model"])
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(next(replies)))

    model = GuardedChatOpenAI(
        model="m", api_key="k", base_url="https://llm.test/v1", streaming=True, max_retries=0,
        http_async_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )  # fmt: skip
    container = await make_container(make_settings(model_max_retries=2), chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, "tóm tắt repo")
    assert kinds(events)[-1] == "done" and len(calls) == 2
    assert len(data_of(events, "retract")) == 1
    assert container.db.messages[-1].content == "Câu trả lời sạch [E1]."


# --- wrap-up: a requested diagram is drawn and an answer written before the budget runs out ------------------


async def test_budget_wrap_up_forces_the_diagram_then_the_answer():
    from agent_harness.domain.policies import ExecutionLimits

    model = ScriptedChatModel(
        responses=[
            tool_call("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/routes.py"}, "a"),
            tool_call("github_list_tree", {"url": REPO_URL}, "b"),
            tool_call("architecture_diagram", DIAGRAM, "c"),  # 2 calls left: only this tool is offered
            tool_call("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/store.py"}, "d"),
            AIMessage("Tóm tắt kiến trúc [E1]."),  # last call: no tools, told to answer now
        ]
    )
    settings = make_settings(limits=ExecutionLimits(max_model_calls=5, retry_backoff_s=0))
    container = await make_container(settings, chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, ASK)
    assert kinds(events)[-1] == "done"
    assert len(model.seen_tools[0]) > 1 and len(model.seen_tools[1]) > 1
    assert model.seen_tools[2] == ["architecture_diagram"] and model.seen_tool_choice[2] == "required"
    assert model.seen_tool_choice[1] is None and model.seen_tool_choice[3] is None
    assert "architecture_diagram" in model.seen_tools[3] and len(model.seen_tools[3]) > 1  # drawn: back to normal
    assert "NGÂN SÁCH ĐÃ HẾT" in model.seen[4][0].content and "NGÂN SÁCH" not in model.seen[3][0].content
    assert "```mermaid" in container.db.messages[-1].content


async def test_answer_without_the_requested_diagram_gets_one_reminder():
    model = ScriptedChatModel(
        responses=[
            tool_call("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/routes.py"}, "a"),
            AIMessage("```mermaid\ngraph TD\n  API[API (FastAPI)] --> DB\n```"),  # hand-written, tool not called
            tool_call("architecture_diagram", DIAGRAM, "b"),
            AIMessage("Sơ đồ ở dưới [E1]."),
        ]
    )
    container = await make_container(chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, ASK)
    assert kinds(events)[-1] == "done"
    assert "Do not write Mermaid yourself" in model.seen[2][-1].content  # the reminder
    answer = container.db.messages[-1].content
    assert "graph TD" not in answer and "look: handDrawn" in answer


async def test_a_stalled_provider_fails_over_to_the_fallback_model():
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "primary":
            raise httpx2.ReadTimeout("no bytes for idle_timeout_s", request=request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse("Trả lời từ model dự phòng [E1]."))

    def make(name: str) -> GuardedChatOpenAI:
        return GuardedChatOpenAI(
            model=name, api_key="k", base_url="https://llm.test/v1", streaming=True, max_retries=0,
            http_async_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )  # fmt: skip

    container = await make_container(make_settings(model_max_retries=1), chat_model=make("primary"), fallback_models=[make("fallback")])
    async with client(container) as http:
        _, events = await chat(http, "tóm tắt repo")
    assert kinds(events)[-1] == "done"
    assert calls == ["primary", "primary", "fallback"]  # one retry of the stalled model, then the fallback
    assert container.db.messages[-1].content == "Trả lời từ model dự phòng [E1]."
