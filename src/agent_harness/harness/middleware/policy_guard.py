"""Enforces rules R1, R2 and R4 on every tool call, whatever the model decided."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_harness.domain.errors import PolicyDenied
from agent_harness.harness.middleware.base import NextTool
from agent_harness.harness.middleware.tool_scope import allowed_tool_names
from agent_harness.harness.tools.registry import ToolRegistry
from agent_harness.harness.tools.spec import EXPORT, ToolCall, ToolResult

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext


class PolicyGuardMiddleware:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult:
        spec = call.spec
        assert spec is not None
        if EXPORT in spec.tags and not ctx.export_allowed:
            raise PolicyDenied("export requires an explicit request from the user in this turn")
        if call.name not in allowed_tool_names(ctx, self._registry, in_flight=1):  # counted by the limit middleware
            skill = ctx.active_skill.name if ctx.active_skill else None
            raise PolicyDenied(f"tool '{call.name}' is not allowed here", active_skill=skill)
        if spec.url_field:
            url = call.args.get(spec.url_field)
            if isinstance(url, str):
                ctx.source_policy.check_url(url)
        return await next(call, ctx)
