from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from agent_harness.harness.tools.spec import ToolCall, ToolResult

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext

NextTool = Callable[[ToolCall, "RunContext"], Awaitable[ToolResult]]


class ToolMiddleware(Protocol):
    """One layer around tool execution. Call `next` to continue, or return a result to short-circuit."""

    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult: ...
