"""In-memory persistence. Same contract as `adapters.postgres` (tests/contract)."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from agent_harness.domain.conversation import Artifact, Conversation, Message, ToolInvocation, Turn, utcnow
from agent_harness.domain.evidence import Evidence


@dataclass
class MemoryDatabase:
    conversations: dict[UUID, Conversation] = field(default_factory=dict)
    turns: dict[UUID, Turn] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    evidence: list[tuple[UUID, UUID, Evidence]] = field(default_factory=list)
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    artifacts: dict[UUID, Artifact] = field(default_factory=dict)
    cache: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)


def _require_conversation(db: MemoryDatabase, conversation_id: UUID) -> None:
    if conversation_id not in db.conversations:
        raise LookupError(f"conversation {conversation_id} does not exist")  # mimics the FK violation


class MemoryConversations:
    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def add(self, conversation: Conversation) -> None:
        self._db.conversations[conversation.id] = copy.deepcopy(conversation)

    async def get(self, conversation_id: UUID) -> Conversation | None:
        found = self._db.conversations.get(conversation_id)
        return copy.deepcopy(found) if found else None

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[Conversation]:
        items = sorted(self._db.conversations.values(), key=lambda c: c.updated_at, reverse=True)
        return [copy.deepcopy(c) for c in items[offset : offset + limit]]

    async def touch(self, conversation_id: UUID, *, title: str | None = None) -> None:
        conversation = self._db.conversations[conversation_id]
        conversation.updated_at = utcnow()
        if title is not None:
            conversation.title = title


class MemoryTurns:
    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def add(self, turn: Turn) -> None:
        _require_conversation(self._db, turn.conversation_id)
        self._db.turns[turn.id] = copy.deepcopy(turn)

    async def update(self, turn: Turn) -> None:
        self._db.turns[turn.id] = copy.deepcopy(turn)

    async def get(self, turn_id: UUID) -> Turn | None:
        found = self._db.turns.get(turn_id)
        return copy.deepcopy(found) if found else None


class MemoryMessages:
    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def add(self, message: Message) -> None:
        _require_conversation(self._db, message.conversation_id)
        self._db.messages.append(copy.deepcopy(message))

    async def list(self, conversation_id: UUID) -> list[Message]:
        return [copy.deepcopy(m) for m in self._db.messages if m.conversation_id == conversation_id]


class MemoryEvidence:
    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def add_many(self, conversation_id: UUID, turn_id: UUID, items: list[Evidence]) -> None:
        existing = {(c, e.label) for c, _, e in self._db.evidence}
        for item in items:
            if (conversation_id, item.label) in existing:
                raise ValueError(f"duplicate evidence label {item.label}")
            self._db.evidence.append((conversation_id, turn_id, item))

    async def list(self, conversation_id: UUID) -> list[Evidence]:
        items = [e for c, _, e in self._db.evidence if c == conversation_id]
        return sorted(items, key=lambda e: int(e.label[1:]))


class MemoryToolInvocations:
    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def add_many(self, items: list[ToolInvocation]) -> None:
        self._db.tool_invocations.extend(copy.deepcopy(items))

    async def list(self, turn_id: UUID) -> list[ToolInvocation]:
        return [copy.deepcopy(i) for i in self._db.tool_invocations if i.turn_id == turn_id]


class MemoryArtifacts:
    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def add(self, artifact: Artifact) -> None:
        _require_conversation(self._db, artifact.conversation_id)
        self._db.artifacts[artifact.id] = copy.deepcopy(artifact)

    async def get(self, artifact_id: UUID) -> Artifact | None:
        found = self._db.artifacts.get(artifact_id)
        return copy.deepcopy(found) if found else None

    async def list(self, conversation_id: UUID) -> list[Artifact]:
        items = [a for a in self._db.artifacts.values() if a.conversation_id == conversation_id]
        return [copy.deepcopy(a) for a in sorted(items, key=lambda a: a.created_at)]


class _Recorder:
    """Applies writes to the UoW's working copy and records them for replay on commit."""

    def __init__(self, repo: Any, ops: list[tuple[str, str, tuple, dict]], name: str) -> None:
        self._repo = repo
        self._ops = ops
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        method = getattr(self._repo, attr)
        if attr in ("get", "list"):
            return method

        async def write(*args: Any, **kwargs: Any) -> Any:
            result = await method(*args, **kwargs)
            self._ops.append((self._name, attr, copy.deepcopy(args), copy.deepcopy(kwargs)))
            return result

        return write


_REPOS = {
    "conversations": MemoryConversations,
    "turns": MemoryTurns,
    "messages": MemoryMessages,
    "evidence": MemoryEvidence,
    "tool_invocations": MemoryToolInvocations,
    "artifacts": MemoryArtifacts,
}


class MemoryUnitOfWork:
    """Reads see a private working copy; commit replays the recorded writes on the shared database,
    so concurrent units of work do not overwrite each other."""

    def __init__(self, db: MemoryDatabase) -> None:
        self._committed = db

    async def __aenter__(self) -> Self:
        await self.rollback()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._ops.clear()

    async def commit(self) -> None:
        for name, method, args, kwargs in self._ops:
            await getattr(_REPOS[name](self._committed), method)(*args, **kwargs)
        self._ops.clear()

    async def rollback(self) -> None:
        self._ops: list[tuple[str, str, tuple, dict]] = []
        work = copy.deepcopy(self._committed)
        for name, repo in _REPOS.items():
            setattr(self, name, _Recorder(repo(work), self._ops, name))


class MemorySourceCache:
    def __init__(self, db: MemoryDatabase | None = None) -> None:
        self._db = db or MemoryDatabase()

    async def get(self, key: str) -> dict[str, Any] | None:
        entry = self._db.cache.get(key)
        if entry is None or entry[0] < time.time():
            return None
        return copy.deepcopy(entry[1])

    async def put(self, key: str, *, kind: str, url: str, payload: dict[str, Any], ttl_s: int) -> None:
        self._db.cache[key] = (time.time() + ttl_s, copy.deepcopy(payload))
