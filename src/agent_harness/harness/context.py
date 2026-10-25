from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from agent_harness.domain.conversation import Intent, ToolInvocation, Usage
from agent_harness.domain.errors import HarnessError
from agent_harness.domain.events import AgentEvent, EventType
from agent_harness.domain.policies import ExecutionLimits, ExportPolicy, SourcePolicy
from agent_harness.domain.skills import SkillLimits, SkillManifest
from agent_harness.harness.evidence_store import EvidenceStore


@dataclass(eq=False)
class RunContext:
    """Everything one turn needs. Created by the application layer, read by harness, tools and engines."""

    conversation_id: UUID
    turn_id: UUID
    message: str
    intent: Intent
    limits: ExecutionLimits
    export_policy: ExportPolicy
    source_policy: SourcePolicy
    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    active_skill: SkillManifest | None = None
    system_prompt_builder: Callable[[RunContext], str] | None = None
    usage: Usage = field(default_factory=Usage)
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    #: Markdown (mermaid block + step table) of each diagram drawn this turn; see DiagramFinalizer.
    diagrams: list[str] = field(default_factory=list)
    #: Markdown a tool already streamed as the start of the answer (e.g. the system map's tables); see
    #: AnswerPrefixFinalizer, which keeps it at the start of the persisted answer.
    answer_prefix: list[str] = field(default_factory=list)
    #: Highest budget a skill may raise this turn to; None = limits cannot be raised.
    limit_ceiling: ExecutionLimits | None = None
    #: Working notes the model saved with `save_note`; re-injected into every system prompt so they
    #: survive context clearing during long reads.
    notes: list[dict[str, Any]] = field(default_factory=list)
    timeout_scope: asyncio.Timeout | None = None
    started_at: float = 0.0
    #: Set when a fatal error happened inside a tool; engines must stop calling the model.
    fatal: HarnessError | None = None
    final_text: str = ""
    events: asyncio.Queue[AgentEvent | None] = field(default_factory=asyncio.Queue)

    def emit(self, event_type: EventType, **data: Any) -> None:
        self.events.put_nowait(AgentEvent(event_type, data))

    def seconds_left(self) -> float | None:
        """Time until the turn deadline (a skill may have extended it); None outside a running turn."""
        when = self.timeout_scope.when() if self.timeout_scope is not None else None
        return None if when is None else when - asyncio.get_running_loop().time()

    def render_system_prompt(self) -> str:
        return self.system_prompt_builder(self) if self.system_prompt_builder else ""

    def raise_limits(self, requested: SkillLimits | None) -> dict[str, Any]:
        """Raise (never lower) the turn budget up to the ceiling. Returns the fields that changed."""
        if requested is None or self.limit_ceiling is None:
            return {}
        changes: dict[str, Any] = {}
        for name in ("max_model_calls", "max_tool_calls", "turn_timeout_s"):
            wanted = getattr(requested, name)
            if wanted is None:
                continue
            value = min(wanted, getattr(self.limit_ceiling, name))
            if value > getattr(self.limits, name):
                changes[name] = value
        if changes:
            self.limits = self.limits.model_copy(update=changes)
            if "turn_timeout_s" in changes and self.timeout_scope is not None:
                self.timeout_scope.reschedule(self.started_at + changes["turn_timeout_s"])
        return changes

    @property
    def export_allowed(self) -> bool:
        return self.export_policy.allows(self.intent)
