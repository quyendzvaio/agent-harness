"""Chat model for any OpenAI-compatible endpoint (Groq, OpenRouter, vLLM, ...), chosen by `BASE_URL`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.exceptions import ModelError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent_harness.adapters.models import output_guard

#: Re-check the streamed text after this many new characters.
_CHECK_EVERY_CHARS = 200


class DegenerateOutputError(ModelError):
    """The model leaked its reasoning or looped. Retryable: the next attempt (or fallback model) usually
    answers normally, and the engine retracts the streamed partial text."""

    is_retryable = True


class GuardedChatOpenAI(ChatOpenAI):
    """`ChatOpenAI` that aborts a call as soon as its output is degenerate (see `output_guard`)."""

    async def _astream(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:  # fmt: skip
        text = ""
        checked = 0
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            content = chunk.message.content
            if isinstance(content, str) and content:
                text += content
                if len(text) - checked >= _CHECK_EVERY_CHARS:
                    checked = len(text)
                    _raise_if_degenerate(text)
            yield chunk
        _raise_if_degenerate(text)

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:  # fmt: skip
        result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        for generation in result.generations:
            if isinstance(generation.message.content, str):
                _raise_if_degenerate(generation.message.content)
        return result


def _raise_if_degenerate(text: str) -> None:
    reason = output_guard.check(text)
    if reason is not None:
        raise DegenerateOutputError(f"degenerate model output ({reason})")


def openai_compatible_model(
    name: str,
    *,
    api_key: SecretStr | None,
    base_url: str,
    temperature: float = 0.2,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
    guard_output: bool = True,
) -> BaseChatModel:
    if api_key is None:
        raise ValueError("LLM_API_KEY is required for the chat model")
    cls = GuardedChatOpenAI if guard_output else ChatOpenAI
    return cls(
        model=name,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        streaming=True,
        stream_usage=True,
        # The harness has its own turn timeout, model retry and fallback, so keep SDK retries short.
        max_retries=1,
        timeout=120,  # seconds
        reasoning_effort=reasoning_effort,
        # A loop the guard misses still ends here.
        max_tokens=max_output_tokens,
    )
