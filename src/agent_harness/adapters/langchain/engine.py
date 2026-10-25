from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
)
from langchain_core.exceptions import ModelError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from agent_harness.adapters.langchain.bridge import HarnessModelMiddleware, to_langchain_tools
from agent_harness.domain.events import EventType
from agent_harness.domain.language import lacks_vietnamese
from agent_harness.harness.context import RunContext
from agent_harness.harness.middleware.limits import ModelGuard
from agent_harness.harness.observability.logger import get_logger
from agent_harness.harness.tools.executor import ToolExecutor

log = get_logger(__name__)

MODEL_NODE = "model"
#: Tool outputs that orient the model for the whole turn; never cleared from context.
_KEEP_TOOLS = ("load_skill", "read_skill_resource", "save_note", "github_repo_overview", "github_dependency_graph", "github_system_map")
_CLEARED = "[đã xoá khỏi context để tiết kiệm token; nhãn [E#] của kết quả này vẫn hợp lệ, xem lại bằng tool nếu cần]"


class LangChainAgentEngine:
    """`AgentEngine` on LangChain `create_agent` (free tool-calling loop over LangGraph).

    The agent is assembled per turn so tools and middleware close over that turn's `RunContext`;
    conversation memory lives in the checkpointer under `thread_id = conversation_id`.
    """

    def __init__(
        self,
        *,
        model: BaseChatModel | Callable[[], BaseChatModel],
        executor: ToolExecutor,
        checkpointer: BaseCheckpointSaver,
        fallback_models: Sequence[BaseChatModel] = (),
        guard: ModelGuard | None = None,
        context_clear_trigger_tokens: int = 0,
        model_retries: int = 0,
        guard_answers: bool = False,
    ) -> None:
        self._model = model
        self._executor = executor
        self._checkpointer = checkpointer
        self._fallbacks = list(fallback_models)
        self._guard = guard or ModelGuard()
        self._clear_trigger = context_clear_trigger_tokens
        self._model_retries = model_retries
        self._guard_answers = guard_answers

    async def run(self, ctx: RunContext) -> str:
        model = self._model() if callable(self._model) and not isinstance(self._model, BaseChatModel) else self._model
        middleware: list[Any] = [HarnessModelMiddleware(ctx, self._executor, self._guard)]
        if self._clear_trigger > 0:
            middleware.append(
                ContextEditingMiddleware(
                    edits=[
                        ClearToolUsesEdit(
                            trigger=self._clear_trigger, keep=6, exclude_tools=_KEEP_TOOLS, placeholder=_CLEARED
                        )
                    ]
                )
            )
        if self._fallbacks:
            middleware.append(ModelFallbackMiddleware(*self._fallbacks))
        if self._model_retries > 0:
            # Innermost (listed last): each model, primary or fallback, is retried before falling back.
            # Retries happen inside one ModelGuard-counted call, so they do not spend the turn's budget.
            middleware.append(
                ModelRetryMiddleware(
                    max_retries=self._model_retries,
                    retry_on=_retryable,
                    on_failure="error",
                    initial_delay=1.0,
                    max_delay=10.0,
                )
            )
        if ctx.intent.wants_diagram:
            middleware.append(_DiagramNudge(ctx))
        if self._guard_answers:
            middleware.append(_AnswerGuard(ctx.intent.language))  # inside the retry, so a bad answer is retried
        agent = create_agent(
            model,
            to_langchain_tools(self._executor, ctx),
            middleware=middleware,
            checkpointer=self._checkpointer,
        )
        config = {
            "configurable": {"thread_id": str(ctx.conversation_id)},
            # A skill may raise the budget mid-turn, so size the graph for the ceiling; ModelGuard enforces the real limit.
            "recursion_limit": 2 * (ctx.limit_ceiling or ctx.limits).max_model_calls + 6,
        }
        final = ""
        streamed_ids: set[str] = set()
        #: Message streamed as tokens whose model call has not completed yet.
        open_id: str | None = None
        async for mode, chunk in agent.astream(
            {"messages": [HumanMessage(ctx.message)]}, config, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                message, meta = chunk
                if meta.get("langgraph_node") == MODEL_NODE and isinstance(message, AIMessageChunk):
                    message_id = message.id or ""
                    if open_id is not None and message_id != open_id:
                        # A new call started before the streamed one completed: it failed mid-stream and
                        # is being retried (or replaced by a fallback model). Its partial text is void.
                        ctx.emit(EventType.RETRACT, message_id=open_id)
                        open_id = None
                    text = _text(message.content)
                    if text:
                        streamed_ids.add(message_id)
                        open_id = message_id
                        ctx.emit(EventType.TOKEN, text=text, message_id=message_id)
            elif mode == "updates":
                for node, update in chunk.items():
                    if node != MODEL_NODE or not update:
                        continue
                    for message in update.get("messages", []):
                        if not isinstance(message, AIMessage):
                            continue
                        if (message.id or "") == open_id:
                            open_id = None
                        if message.tool_calls and (message.id or "") in streamed_ids:
                            # Text next to a tool call is the model narrating its plan, not the answer (and it is
                            # not persisted): take it back so the client shows what a reload will show.
                            ctx.emit(EventType.RETRACT, message_id=message.id or "")
                        if not message.tool_calls:
                            final = _text(message.content)
                            if final and (message.id or "") not in streamed_ids:
                                ctx.emit(
                                    EventType.TOKEN, text=final, message_id=message.id or ""
                                )  # non-streaming model
        return final


class UnusableAnswerError(ModelError):
    """A final answer that is not one: empty, or an English planning note in a Vietnamese turn ("Read db create_all.")."""

    is_retryable = True


class _AnswerGuard(AgentMiddleware):
    def __init__(self, language: str) -> None:
        super().__init__()
        self._language = language

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        response = await handler(request)
        for message in getattr(response, "result", None) or [response]:
            if not isinstance(message, AIMessage) or message.tool_calls:
                continue
            text = _text(message.content)
            if not text.strip():
                raise UnusableAnswerError("empty final answer")
            if self._language == "vi" and lacks_vietnamese(text):
                raise UnusableAnswerError(f"final answer is not Vietnamese: {text[:80]!r}")
        return response


DIAGRAM_TOOL = "architecture_diagram"
_DIAGRAM_NUDGE = (
    "The user asked for a diagram and you have not drawn it. Call the `architecture_diagram` tool now with the "
    "runtime components and numbered flows you found (cite only existing [E#] labels). Do not write Mermaid yourself."
)


class _DiagramNudge(AgentMiddleware):
    """A requested diagram is drawn by the tool, not hand-written: if the model answers while the tool is offered
    and nothing was drawn yet, ask once more with a reminder (the first answer's streamed text is retracted)."""

    def __init__(self, ctx: RunContext) -> None:
        super().__init__()
        self._ctx = ctx

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        response = await handler(request)
        offered = {getattr(t, "name", None) for t in request.tools}
        if self._ctx.diagrams or DIAGRAM_TOOL not in offered or not _is_final_answer(response):
            return response
        log.warning("diagram_nudge", extra={"turn_id": str(self._ctx.turn_id)})
        return await handler(request.override(messages=[*request.messages, HumanMessage(_DIAGRAM_NUDGE)]))


def _is_final_answer(response: Any) -> bool:
    messages = [m for m in getattr(response, "result", None) or [response] if isinstance(m, AIMessage)]
    return bool(messages) and not any(m.tool_calls for m in messages)


def _retryable(exc: Exception) -> bool:
    """Connection drops, timeouts, rate limits and 5xx (provider-agnostic `ModelError.is_retryable`).
    A wrong model name, key or request fails at once."""
    retry = isinstance(exc, ModelError) and exc.is_retryable
    if retry:
        log.warning("model_call_retry", extra={"error": f"{type(exc).__name__}: {exc}"[:300]})
    return retry


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return ""
