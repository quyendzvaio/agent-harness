from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agent_harness.domain.errors import TransientError
from agent_harness.harness.middleware.base import NextTool
from agent_harness.harness.tools.spec import ToolCall, ToolResult

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext


class RetryMiddleware:
    """Retries only `TransientError`, with exponential backoff from the turn's limits."""

    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult:
        attempt = 0
        while True:
            try:
                return await next(call, ctx)
            except TransientError:
                if attempt >= ctx.limits.max_retries:
                    raise
                await asyncio.sleep(ctx.limits.retry_backoff_s * (2**attempt))
                attempt += 1
