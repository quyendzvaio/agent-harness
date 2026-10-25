"""Bridges harness concepts into LangChain: tools run through `ToolExecutor`, model calls through `ModelGuard`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic_core import to_jsonable_python

from agent_harness.harness.context import RunContext
from agent_harness.harness.middleware.limits import ModelGuard
from agent_harness.harness.middleware.tool_scope import allowed_tool_names, wrap_up
from agent_harness.harness.tools.executor import ToolExecutor
from agent_harness.harness.tools.spec import ToolCall, ToolSpec


def to_langchain_tools(executor: ToolExecutor, ctx: RunContext) -> list[BaseTool]:
    """One LangChain tool per harness tool, bound to this turn's context.

    The whole harness middleware chain (policy, limits, retry, audit, ...) runs inside `execute`.
    Non-fatal errors go back to the model as text; fatal ones (limits) abort the run.
    """

    def make(spec: ToolSpec) -> BaseTool:
        async def run(**kwargs: Any) -> str:
            # LangChain hands nested arguments over as pydantic objects; the harness (audit log, persistence, SSE)
            # works with plain JSON.
            result = await executor.execute(ToolCall(spec.name, to_jsonable_python(kwargs)), ctx)
            if result.error is not None and result.error.fatal:
                raise result.error
            return result.content

        return StructuredTool.from_function(
            coroutine=run, name=spec.name, description=spec.description, args_schema=spec.input_model
        )

    return [make(spec) for spec in executor.registry.all()]


class HarnessModelMiddleware(AgentMiddleware):
    """Applies, on every model call: limits (R3), tool scoping (R2/R4) and the dynamic system prompt."""

    def __init__(self, ctx: RunContext, executor: ToolExecutor, guard: ModelGuard) -> None:
        super().__init__()
        self._ctx = ctx
        self._registry = executor.registry
        self._guard = guard

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | AIMessage:
        ctx = self._ctx
        self._guard.before_model(ctx)
        allowed = allowed_tool_names(ctx, self._registry)
        tools = [t for t in request.tools if getattr(t, "name", None) in allowed]
        request = request.override(
            tools=tools,
            system_message=SystemMessage(ctx.render_system_prompt()),
            # The diagram is due and it is the only tool left: deep into a long turn some models keep calling
            # the tools they used before, so make the call mandatory.
            **({"tool_choice": "required"} if wrap_up(ctx) == "diagram" and tools else {}),
        )
        response = await handler(request)
        messages = response.result if isinstance(response, ModelResponse) else [response]
        for message in messages:
            if isinstance(message, AIMessage):
                usage = message.usage_metadata or {}
                self._guard.after_model(
                    ctx,
                    tokens_in=usage.get("input_tokens", 0),
                    tokens_out=usage.get("output_tokens", 0),
                    model=message.response_metadata.get("model_name"),
                )
        return response
