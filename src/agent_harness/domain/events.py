"""Events a turn emits. The API layer serializes them to SSE; nothing here knows about HTTP."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    CONVERSATION = "conversation"
    TOKEN = "token"
    #: Text of `message_id` already streamed as tokens is not part of the answer: that model call failed and
    #: is retried, or the text was narration next to a tool call (never persisted).
    RETRACT = "retract"
    SKILL_LOADED = "skill_loaded"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    CITATION_REPORT = "citation_report"
    ARTIFACT = "artifact"
    ERROR = "error"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.type in (EventType.DONE, EventType.ERROR)
