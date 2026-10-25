from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from agent_harness.domain.conversation import Artifact, Conversation, Message
from agent_harness.domain.evidence import Evidence
from agent_harness.domain.skills import SkillManifest


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: UUID | None = None


class ConversationOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, c: Conversation) -> ConversationOut:
        return cls(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    turn_id: UUID | None
    created_at: datetime
    metadata: dict[str, Any]

    @classmethod
    def of(cls, m: Message) -> MessageOut:
        return cls(id=m.id, role=m.role.value, content=m.content, turn_id=m.turn_id, created_at=m.created_at, metadata=m.metadata)


class EvidenceOut(BaseModel):
    label: str
    kind: str
    url: str
    locator: str
    quote: str

    @classmethod
    def of(cls, e: Evidence) -> EvidenceOut:
        return cls(label=e.label, kind=e.source.kind.value, url=e.source.url, locator=e.locator, quote=e.quote)


class ArtifactOut(BaseModel):
    id: UUID
    conversation_id: UUID
    turn_id: UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime

    @classmethod
    def of(cls, a: Artifact) -> ArtifactOut:
        return cls(
            id=a.id, conversation_id=a.conversation_id, turn_id=a.turn_id, filename=a.filename,
            media_type=a.media_type, size_bytes=a.size_bytes, sha256=a.sha256, created_at=a.created_at,
        )  # fmt: skip


class SkillOut(BaseModel):
    name: str
    description: str
    source_types: list[str]
    allowed_tools: list[str]
    resources: list[str]
    trust: str
    limits: dict[str, Any] | None = None
    hooks_enabled: bool
    instructions: str | None = None

    @classmethod
    def of(cls, s: SkillManifest, *, detail: bool = False) -> SkillOut:
        return cls(**s.summary(), instructions=s.instructions if detail else None)
