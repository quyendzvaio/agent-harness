"""Orchestration port. Implementations live in `adapters` (LangChain) and `harness.scripted` (tests)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext


class AgentEngine(Protocol):
    async def run(self, ctx: RunContext) -> str:
        """Run one turn to completion and return the final answer.

        Engines emit `token` events through `ctx.emit`, run every tool through the harness
        `ToolExecutor`, and call `ModelGuard.before_model/after_model` around each model call.
        """
        ...


class Finalizer(Protocol):
    """Post-processing of the final answer (citation check, skill `on_final` hooks)."""

    async def __call__(self, ctx: RunContext, text: str) -> str: ...
