from __future__ import annotations

import time
from typing import TYPE_CHECKING

from agent_harness.domain.conversation import ToolInvocation
from agent_harness.domain.events import EventType
from agent_harness.harness.middleware.base import NextTool
from agent_harness.harness.observability.logger import get_logger, redact
from agent_harness.harness.tools.spec import ToolCall, ToolResult

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext

log = get_logger(__name__)


class AuditLogMiddleware:
    """Outermost layer: emits tool events, records an invocation row, logs the call."""

    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult:
        safe_input = redact(call.args)
        ctx.emit(EventType.TOOL_STARTED, call_id=call.call_id, tool=call.name, input=safe_input)
        started = time.perf_counter()
        result = await next(call, ctx)
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        ctx.tool_invocations.append(
            ToolInvocation(
                turn_id=ctx.turn_id,
                tool_name=call.name,
                input=safe_input,
                status=result.status,
                duration_ms=result.duration_ms,
                error=result.error.to_dict() if result.error else None,
            )
        )
        ctx.emit(
            EventType.TOOL_FINISHED,
            call_id=call.call_id,
            tool=call.name,
            status=result.status,
            evidence_labels=list(result.evidence_labels),
            duration_ms=result.duration_ms,
        )
        log.info(
            "tool_call",
            extra={
                "conversation_id": str(ctx.conversation_id),
                "turn_id": str(ctx.turn_id),
                "tool": call.name,
                "status": result.status,
                "duration_ms": result.duration_ms,
            },
        )
        return result
