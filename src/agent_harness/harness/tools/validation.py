from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from agent_harness.domain.errors import InvalidInput
from agent_harness.harness.middleware.base import NextTool
from agent_harness.harness.tools.spec import ToolCall, ToolResult

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext


class ValidationMiddleware:
    """Innermost layer: parses the model's arguments with the tool's pydantic input model."""

    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult:
        assert call.spec is not None
        try:
            call.parsed = call.spec.input_model.model_validate(call.args)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
            raise InvalidInput(problems) from exc
        return await next(call, ctx)
