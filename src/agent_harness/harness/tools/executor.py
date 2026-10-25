from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent_harness.domain.errors import InvalidInput
from agent_harness.harness.middleware.audit_log import AuditLogMiddleware
from agent_harness.harness.middleware.base import NextTool, ToolMiddleware
from agent_harness.harness.middleware.error_boundary import ErrorBoundaryMiddleware
from agent_harness.harness.middleware.limits import ToolLimitMiddleware
from agent_harness.harness.middleware.policy_guard import PolicyGuardMiddleware
from agent_harness.harness.middleware.retry import RetryMiddleware
from agent_harness.harness.middleware.untrusted_content import UntrustedContentMiddleware
from agent_harness.harness.tools.registry import ToolRegistry
from agent_harness.harness.tools.spec import ToolCall, ToolOutput, ToolResult
from agent_harness.harness.tools.validation import ValidationMiddleware

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext


def default_middleware(registry: ToolRegistry, extra: Sequence[ToolMiddleware] = ()) -> list[ToolMiddleware]:
    """Standard chain, outermost first. `extra` layers run just before validation."""
    return [
        AuditLogMiddleware(),
        ErrorBoundaryMiddleware(),
        ToolLimitMiddleware(),
        PolicyGuardMiddleware(registry),
        RetryMiddleware(),
        UntrustedContentMiddleware(),
        *extra,
        ValidationMiddleware(),
    ]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, middleware: Sequence[ToolMiddleware]) -> None:
        self.registry = registry
        self._middleware = list(middleware)

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        call.spec = self.registry.get(call.name)
        if call.spec is None:
            return ToolResult.failure(call, InvalidInput(f"unknown tool '{call.name}'"))
        chain: NextTool = _invoke_handler
        for layer in reversed(self._middleware):
            chain = _bind(layer, chain)
        return await chain(call, ctx)


def _bind(layer: ToolMiddleware, next_: NextTool) -> NextTool:
    async def run(call: ToolCall, ctx: RunContext) -> ToolResult:
        return await layer(call, ctx, next_)

    return run


async def _invoke_handler(call: ToolCall, ctx: RunContext) -> ToolResult:
    assert call.spec is not None and call.parsed is not None
    output = await call.spec.handler(call.parsed, ctx)
    if isinstance(output, str):
        output = ToolOutput(output)
    return ToolResult(call.call_id, call.name, True, output.content, evidence_labels=output.evidence_labels, data=output.data)
