"""Deterministic `AgentEngine` without any LLM framework.

Used by tests and offline demos, and as the proof that the API/application layers do not depend
on LangChain: swapping engines is a one-line change in `bootstrap`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_harness.domain.events import EventType
from agent_harness.harness.context import RunContext
from agent_harness.harness.middleware.limits import ModelGuard
from agent_harness.harness.tools.executor import ToolExecutor
from agent_harness.harness.tools.spec import ToolCall


@dataclass(frozen=True, slots=True)
class ToolStep:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TextStep:
    text: str


Step = ToolStep | TextStep
Script = Sequence[Step] | Callable[[RunContext], Sequence[Step]]


class ScriptedEngine:
    def __init__(self, executor: ToolExecutor, script: Script, guard: ModelGuard | None = None) -> None:
        self._executor = executor
        self._script = script
        self._guard = guard or ModelGuard()

    async def run(self, ctx: RunContext) -> str:
        steps = self._script(ctx) if callable(self._script) else self._script
        answer: list[str] = []
        for step in steps:
            self._guard.before_model(ctx)  # every step stands for one model decision
            self._guard.after_model(ctx, model="scripted")
            if isinstance(step, ToolStep):
                await self._executor.execute(ToolCall(step.name, dict(step.args)), ctx)
                if ctx.fatal is not None:
                    raise ctx.fatal
            else:
                words = step.text.split(" ")
                for i, word in enumerate(words):
                    ctx.emit(EventType.TOKEN, text=word if i == len(words) - 1 else word + " ")
                answer.append(step.text)
        return "\n".join(answer)
