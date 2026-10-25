"""Deterministic chat model for tests: replays a list of AI messages, including tool calls."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

Responder = Callable[[list[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    responses: list[Any] = Field(default_factory=list)  # AIMessage | Exception | Responder
    name_tag: str = "scripted"
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    seen_tools: list[list[str]] = Field(default_factory=list)
    bound_tools: list[str] = Field(default_factory=list)
    tool_choice: Any = None
    seen_tool_choice: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        self.bound_tools = [getattr(t, "name", None) or t.get("name") or t["function"]["name"] for t in tools]
        self.tool_choice = kwargs.get("tool_choice")
        return self

    def _next(self, messages: list[BaseMessage]) -> ChatResult:
        self.seen.append(list(messages))
        self.seen_tools.append(list(self.bound_tools))
        self.seen_tool_choice.append(self.tool_choice)
        if not self.responses:
            raise RuntimeError("ScriptedChatModel ran out of responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        message = item(messages) if callable(item) else item
        message = message.model_copy(
            update={
                "response_metadata": {**message.response_metadata, "model_name": self.name_tag},
                "usage_metadata": message.usage_metadata or {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> ChatResult:
        return self._next(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)


def tool_call(name: str, args: dict[str, Any], call_id: str | None = None) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id or f"call_{name}"}])
