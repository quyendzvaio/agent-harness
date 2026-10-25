"""Rule R8: a failing tool becomes a structured result instead of crashing the turn."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agent_harness.domain.errors import HarnessError, ToolFailed
from agent_harness.harness.middleware.base import NextTool
from agent_harness.harness.observability.logger import get_logger
from agent_harness.harness.tools.spec import ToolCall, ToolResult

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext

log = get_logger(__name__)


class ErrorBoundaryMiddleware:
    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult:
        try:
            return await next(call, ctx)
        except asyncio.CancelledError:
            raise
        except HarnessError as exc:
            if exc.fatal:
                ctx.fatal = exc
            return ToolResult.failure(call, exc)
        except Exception as exc:  # noqa: BLE001 - this is the boundary
            log.exception("tool_crashed", extra={"tool": call.name, "turn_id": str(ctx.turn_id)})
            return ToolResult.failure(call, ToolFailed(f"{type(exc).__name__}: {exc}"))
