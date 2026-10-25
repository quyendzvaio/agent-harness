import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.exceptions import ModelConnectionError, ModelNotFoundError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk

from agent_harness.adapters.models.scripted import ScriptedChatModel, tool_call
from agent_harness.domain.errors import ToolFailed
from builders import chat, client, data_of, kinds, make_container, make_settings
from fakes import REPO_URL, FakeGithubClient


async def test_conversation_memory_via_checkpointer_and_dynamic_system_prompt():
    model = ScriptedChatModel(
        responses=[
            tool_call("load_skill", {"name": "github-architecture"}),
            AIMessage("Đã nạp skill."),
            AIMessage("Như đã nói ở lượt trước."),
        ]
    )
    container = await make_container(chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, f"phân tích {REPO_URL}")
        conversation_id = data_of(events, "conversation")[0]["conversation_id"]
        _, events2 = await chat(http, "nhắc lại?", conversation_id)
    assert data_of(events2, "done")

    last_call = model.seen[-1]
    assert isinstance(last_call[0], SystemMessage) and "Skill" in last_call[0].content
    humans = [m.content for m in last_call if isinstance(m, HumanMessage)]
    assert humans == [f"phân tích {REPO_URL}", "nhắc lại?"]  # turn 1 restored from the checkpointer
    assert any(isinstance(m, ToolMessage) and "Skill loaded" in m.content for m in last_call)
    # Only one system message even after several turns: the prompt is injected per call, not stored.
    assert sum(isinstance(m, SystemMessage) for m in last_call) == 1


async def test_tool_results_reach_the_model_wrapped_as_source_data():
    model = ScriptedChatModel(responses=[tool_call("github_list_tree", {"url": REPO_URL}), AIMessage("xong [E1]")])
    container = await make_container(chat_model=model)
    async with client(container) as http:
        await chat(http, f"xem {REPO_URL}")
    tool_message = next(m for m in model.seen[-1] if isinstance(m, ToolMessage))
    assert tool_message.content.startswith("<source_data>") and "[E1] tree:/" in tool_message.content
    assert "node_modules" not in tool_message.content  # the snapshot drops vendored directories


class TooLargeForSnapshot(FakeGithubClient):
    async def get_tarball(self, owner, repo, ref, *, max_bytes):
        raise ToolFailed("response exceeds 60000000 bytes")


async def test_trusted_skill_post_tool_hook_rewrites_tool_output():
    """API fallback (repo too large for a snapshot) lists node_modules; the skill's post_tool hook hides it."""
    model = ScriptedChatModel(
        responses=[
            tool_call("load_skill", {"name": "github-architecture"}, "a"),
            tool_call("github_list_tree", {"url": REPO_URL}, "b"),
            AIMessage("xong [E1]"),
        ]
    )
    container = await make_container(chat_model=model, github=TooLargeForSnapshot())
    async with client(container) as http:
        await chat(http, f"xem {REPO_URL}")
    tree = [m for m in model.seen[-1] if isinstance(m, ToolMessage)][-1].content
    assert "node_modules" not in tree and "hidden" in tree


# --- model call retried after the connection drops mid-stream ----------------------------------------------------


class DropsMidStream(BaseChatModel):
    """Streams; the first `drops` calls send part of the answer, then the connection breaks."""

    answer: str = "Trả lời đầy đủ"
    drops: int = 1
    error: Any = None
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "drops-mid-stream"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        self.calls += 1
        dropping = self.calls <= self.drops
        for word in ("Đang phân", " tích") if dropping else (self.answer,):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=word))
            if run_manager:
                await run_manager.on_llm_new_token(word, chunk=chunk)
            yield chunk
        if dropping:
            raise self.error or ModelConnectionError("peer closed connection without sending complete message body")


async def test_model_call_dropped_mid_stream_is_retried_and_partial_text_retracted():
    model = DropsMidStream()
    container = await make_container(make_settings(model_max_retries=2), chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, "hi")
    assert kinds(events)[-1] == "done" and model.calls == 2
    tokens = data_of(events, "token")
    retracted = {r["message_id"] for r in data_of(events, "retract")}
    assert len(retracted) == 1
    assert "".join(t["text"] for t in tokens if t["message_id"] not in retracted) == "Trả lời đầy đủ"
    assert "".join(t["text"] for t in tokens if t["message_id"] in retracted) == "Đang phân tích"
    assert data_of(events, "done")[0]["usage"]["model_calls"] == 1  # retries do not spend the turn budget
    assert container.db.messages[-1].content == "Trả lời đầy đủ"


async def test_model_retries_give_up_with_model_error():
    model = DropsMidStream(drops=5)
    container = await make_container(make_settings(model_max_retries=1), chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, "hi")
    assert model.calls == 2 and data_of(events, "error")[0]["code"] == "model_error"


async def test_non_transient_model_error_is_not_retried():
    model = DropsMidStream(error=ModelNotFoundError("model 'x' is not supported"))
    container = await make_container(make_settings(model_max_retries=2), chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, "hi")
    assert model.calls == 1 and "ModelNotFoundError" in data_of(events, "error")[0]["message"]


# --- answers that are not answers -------------------------------------------------------------------------------


async def test_english_planning_note_as_final_answer_of_a_vietnamese_turn_is_retried():
    model = ScriptedChatModel(
        responses=[AIMessage("Read db create_all and summarize."), AIMessage("Repo dùng FastAPI.")]
    )
    container = await make_container(make_settings(model_max_retries=2), chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, "tóm tắt repo này")
    assert kinds(events)[-1] == "done" and len(model.seen) == 2
    assert "".join(t["text"] for t in data_of(events, "token")) == "Repo dùng FastAPI."


async def test_english_answers_are_fine_in_an_english_turn():
    model = ScriptedChatModel(responses=[AIMessage("The repository uses FastAPI for its API layer.")])
    container = await make_container(make_settings(model_max_retries=2), chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, "summarize this repository please")
    assert kinds(events)[-1] == "done" and len(model.seen) == 1


class NarratesThenCalls(BaseChatModel):
    """Streams 'narration' next to a tool call, then a final answer (what gpt-oss does)."""

    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "narrates"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        self.calls += 1
        if self.calls == 1:
            chunks = [
                AIMessageChunk(content="We need to read the tree first."),
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": "github_list_tree", "args": json.dumps({"url": REPO_URL}), "id": "t1", "index": 0}
                    ],
                ),
            ]
        else:
            chunks = [AIMessageChunk(content="Cây thư mục có src [E1].")]
        for message in chunks:
            chunk = ChatGenerationChunk(message=message)
            if run_manager and message.content:
                await run_manager.on_llm_new_token(message.content, chunk=chunk)
            yield chunk


async def test_narration_next_to_a_tool_call_is_retracted():
    container = await make_container(chat_model=NarratesThenCalls())
    async with client(container) as http:
        _, events = await chat(http, f"xem {REPO_URL}")
    retracted = {r["message_id"] for r in data_of(events, "retract")}
    shown = "".join(t["text"] for t in data_of(events, "token") if t["message_id"] not in retracted)
    assert shown == "Cây thư mục có src [E1]." and len(retracted) == 1


async def test_empty_final_answer_is_retried():
    model = ScriptedChatModel(responses=[AIMessage(""), AIMessage("Repo dùng FastAPI.")])
    container = await make_container(make_settings(model_max_retries=2), chat_model=model)
    async with client(container) as http:
        _, events = await chat(http, "tóm tắt repo này")
    assert len(model.seen) == 2 and container.db.messages[-1].content == "Repo dùng FastAPI."
