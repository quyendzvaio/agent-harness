"""Rule R6: source content is data, never instructions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_harness.harness.middleware.base import NextTool
from agent_harness.harness.tools.spec import ToolCall, ToolResult

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext

OPEN_TAG = "<source_data>"
CLOSE_TAG = "</source_data>"


class UntrustedContentMiddleware:
    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult:
        result = await next(call, ctx)
        if result.ok and call.spec is not None and call.spec.untrusted_output:
            # A source cannot close the wrapper early and smuggle text outside it.
            body = result.content.replace(CLOSE_TAG, "</source_data_>")
            result.content = f"{OPEN_TAG}\n{body}\n{CLOSE_TAG}"
        return result
