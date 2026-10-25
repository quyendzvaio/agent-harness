from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


def utcnow() -> datetime:
    return datetime.now(UTC)


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class TurnStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class Conversation:
    id: UUID = field(default_factory=uuid4)
    title: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Message:
    conversation_id: UUID
    role: Role
    content: str
    turn_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(slots=True)
class Usage:
    model_calls: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "model": self.model,
        }


@dataclass(slots=True)
class Turn:
    conversation_id: UUID
    id: UUID = field(default_factory=uuid4)
    status: TurnStatus = TurnStatus.RUNNING
    explicit_export: bool = False
    active_skill: str | None = None
    usage: Usage = field(default_factory=Usage)
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolInvocation:
    turn_id: UUID
    tool_name: str
    input: dict[str, Any]
    status: str
    duration_ms: int
    error: dict[str, Any] | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(slots=True)
class Artifact:
    conversation_id: UUID
    turn_id: UUID
    filename: str
    media_type: str
    storage_uri: str
    size_bytes: int
    sha256: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utcnow)

    @property
    def storage_key(self) -> str:
        # storage_uri is "s3://<bucket>/<key>" (or "memory://<key>")
        return self.storage_uri.split("://", 1)[1].split("/", 1)[1]


@dataclass(frozen=True, slots=True)
class Intent:
    """Deterministic reading of the user's message. The model cannot change it."""

    urls: tuple[str, ...] = ()
    explicit_export: bool = False
    #: The message asks for an architecture/flow diagram (enables `architecture_diagram`).
    wants_diagram: bool = False
    language: str = "vi"
