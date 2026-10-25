"""Rule R3: per-turn budgets for tool and model calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_harness.domain.errors import LimitExceeded
from agent_harness.harness.middleware.base import NextTool
from agent_harness.harness.tools.spec import ToolCall, ToolResult

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext


class ToolLimitMiddleware:
    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult:
        if ctx.usage.tool_calls >= ctx.limits.max_tool_calls:
            raise LimitExceeded(f"max_tool_calls={ctx.limits.max_tool_calls} reached")
        ctx.usage.tool_calls += 1
        return await next(call, ctx)


class ModelGuard:
    """Model-side counterpart. Engines call `before_model` / `after_model` around every model call."""

    def before_model(self, ctx: RunContext) -> None:
        if ctx.fatal is not None:
            raise ctx.fatal
        if ctx.usage.model_calls >= ctx.limits.max_model_calls:
            raise LimitExceeded(f"max_model_calls={ctx.limits.max_model_calls} reached")
        ctx.usage.model_calls += 1

    def after_model(self, ctx: RunContext, *, tokens_in: int = 0, tokens_out: int = 0, model: str | None = None) -> None:
        ctx.usage.tokens_in += tokens_in
        ctx.usage.tokens_out += tokens_out
        if model:
            ctx.usage.model = model
