"""Persistence ports. Implementations: `adapters.postgres` (production) and `adapters.memory` (tests).

Both run the same contract test suite (tests/contract/test_repositories.py).
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from agent_harness.domain.conversation import Artifact, Conversation, Message, ToolInvocation, Turn
from agent_harness.domain.evidence import Evidence


class ConversationRepository(Protocol):
    async def add(self, conversation: Conversation) -> None: ...
    async def get(self, conversation_id: UUID) -> Conversation | None: ...
    async def list(self, *, limit: int = 50, offset: int = 0) -> list[Conversation]: ...
    async def touch(self, conversation_id: UUID, *, title: str | None = None) -> None: ...


class TurnRepository(Protocol):
    async def add(self, turn: Turn) -> None: ...
    async def update(self, turn: Turn) -> None: ...
    async def get(self, turn_id: UUID) -> Turn | None: ...


class MessageRepository(Protocol):
    async def add(self, message: Message) -> None: ...
    async def list(self, conversation_id: UUID) -> list[Message]: ...


class EvidenceRepository(Protocol):
    async def add_many(self, conversation_id: UUID, turn_id: UUID, items: list[Evidence]) -> None: ...
    async def list(self, conversation_id: UUID) -> list[Evidence]: ...


class ToolInvocationRepository(Protocol):
    async def add_many(self, items: list[ToolInvocation]) -> None: ...
    async def list(self, turn_id: UUID) -> list[ToolInvocation]: ...


class ArtifactRepository(Protocol):
    async def add(self, artifact: Artifact) -> None: ...
    async def get(self, artifact_id: UUID) -> Artifact | None: ...
    async def list(self, conversation_id: UUID) -> list[Artifact]: ...


class UnitOfWork(Protocol):
    conversations: ConversationRepository
    turns: TurnRepository
    messages: MessageRepository
    evidence: EvidenceRepository
    tool_invocations: ToolInvocationRepository
    artifacts: ArtifactRepository

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]


class SourceCache(Protocol):
    """Cache of fetched source payloads, keyed by a canonical key (rule: fetch once, cite many)."""

    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def put(self, key: str, *, kind: str, url: str, payload: dict[str, Any], ttl_s: int) -> None: ...
