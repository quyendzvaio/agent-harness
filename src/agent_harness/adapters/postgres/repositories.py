from __future__ import annotations

from datetime import timedelta
from types import TracebackType
from typing import Any, Self
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_harness.adapters.postgres import tables as t
from agent_harness.domain.conversation import (
    Artifact,
    Conversation,
    Message,
    Role,
    ToolInvocation,
    Turn,
    TurnStatus,
    Usage,
    utcnow,
)
from agent_harness.domain.evidence import Evidence
from agent_harness.domain.sources import SourceKind, SourceRef


class PgConversations:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, c: Conversation) -> None:
        await self._s.execute(
            t.conversations.insert().values(
                id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at, metadata=c.metadata
            )
        )

    async def get(self, conversation_id: UUID) -> Conversation | None:
        row = (await self._s.execute(sa.select(t.conversations).where(t.conversations.c.id == conversation_id))).first()
        return _conversation(row) if row else None

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[Conversation]:
        query = sa.select(t.conversations).order_by(t.conversations.c.updated_at.desc()).limit(limit).offset(offset)
        return [_conversation(r) for r in await self._s.execute(query)]

    async def touch(self, conversation_id: UUID, *, title: str | None = None) -> None:
        values: dict[str, Any] = {"updated_at": utcnow()}
        if title is not None:
            values["title"] = title
        await self._s.execute(t.conversations.update().where(t.conversations.c.id == conversation_id).values(**values))


class PgTurns:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, turn: Turn) -> None:
        await self._s.execute(t.turns.insert().values(**_turn_values(turn)))

    async def update(self, turn: Turn) -> None:
        values = _turn_values(turn)
        values.pop("id")
        await self._s.execute(t.turns.update().where(t.turns.c.id == turn.id).values(**values))

    async def get(self, turn_id: UUID) -> Turn | None:
        row = (await self._s.execute(sa.select(t.turns).where(t.turns.c.id == turn_id))).first()
        if row is None:
            return None
        return Turn(
            id=row.id,
            conversation_id=row.conversation_id,
            status=TurnStatus(row.status),
            explicit_export=row.explicit_export,
            active_skill=row.active_skill,
            usage=Usage(row.model_calls, row.tool_calls, row.tokens_in, row.tokens_out, row.model),
            started_at=row.started_at,
            finished_at=row.finished_at,
            error=row.error,
            metadata=row.metadata,
        )


class PgMessages:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, m: Message) -> None:
        await self._s.execute(
            t.messages.insert().values(
                id=m.id, conversation_id=m.conversation_id, turn_id=m.turn_id, role=m.role.value,
                content=m.content, metadata=m.metadata, created_at=m.created_at,
            )  # fmt: skip
        )

    async def list(self, conversation_id: UUID) -> list[Message]:
        query = (
            sa.select(t.messages)
            .where(t.messages.c.conversation_id == conversation_id)
            .order_by(t.messages.c.created_at, t.messages.c.id)
        )
        return [
            Message(r.conversation_id, Role(r.role), r.content, turn_id=r.turn_id, id=r.id, metadata=r.metadata, created_at=r.created_at)
            for r in await self._s.execute(query)
        ]


class PgEvidence:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_many(self, conversation_id: UUID, turn_id: UUID, items: list[Evidence]) -> None:
        if not items:
            return
        await self._s.execute(
            t.evidence.insert(),
            [
                {
                    "conversation_id": conversation_id, "turn_id": turn_id, "label": e.label, "ordinal": int(e.label[1:]),
                    "source_kind": e.source.kind.value, "source_url": e.source.url,
                    "source_canonical_id": e.source.canonical_id, "locator": e.locator, "quote": e.quote,
                }  # fmt: skip
                for e in items
            ],
        )

    async def list(self, conversation_id: UUID) -> list[Evidence]:
        query = sa.select(t.evidence).where(t.evidence.c.conversation_id == conversation_id).order_by(t.evidence.c.ordinal)
        return [
            Evidence(r.label, SourceRef(SourceKind(r.source_kind), r.source_url, r.source_canonical_id), r.locator, r.quote)
            for r in await self._s.execute(query)
        ]


class PgToolInvocations:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_many(self, items: list[ToolInvocation]) -> None:
        if not items:
            return
        await self._s.execute(
            t.tool_invocations.insert(),
            [
                {"id": i.id, "turn_id": i.turn_id, "tool_name": i.tool_name, "input": i.input, "status": i.status,
                 "duration_ms": i.duration_ms, "error": i.error, "created_at": i.created_at}  # fmt: skip
                for i in items
            ],
        )

    async def list(self, turn_id: UUID) -> list[ToolInvocation]:
        query = sa.select(t.tool_invocations).where(t.tool_invocations.c.turn_id == turn_id).order_by(t.tool_invocations.c.created_at)
        return [
            ToolInvocation(r.turn_id, r.tool_name, r.input, r.status, r.duration_ms, r.error, id=r.id, created_at=r.created_at)
            for r in await self._s.execute(query)
        ]


class PgArtifacts:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, a: Artifact) -> None:
        await self._s.execute(
            t.artifacts.insert().values(
                id=a.id, conversation_id=a.conversation_id, turn_id=a.turn_id, filename=a.filename,
                media_type=a.media_type, storage_uri=a.storage_uri, size_bytes=a.size_bytes, sha256=a.sha256,
                created_at=a.created_at,
            )  # fmt: skip
        )

    async def get(self, artifact_id: UUID) -> Artifact | None:
        row = (await self._s.execute(sa.select(t.artifacts).where(t.artifacts.c.id == artifact_id))).first()
        return _artifact(row) if row else None

    async def list(self, conversation_id: UUID) -> list[Artifact]:
        query = sa.select(t.artifacts).where(t.artifacts.c.conversation_id == conversation_id).order_by(t.artifacts.c.created_at)
        return [_artifact(r) for r in await self._s.execute(query)]


class PostgresUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        s = self._session
        self.conversations = PgConversations(s)
        self.turns = PgTurns(s)
        self.messages = PgMessages(s)
        self.evidence = PgEvidence(s)
        self.tool_invocations = PgToolInvocations(s)
        self.artifacts = PgArtifacts(s)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        try:
            if exc_type is not None:
                await self._session.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


class PostgresSourceCache:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._session_factory() as s:
            query = sa.select(t.sources.c.payload).where(t.sources.c.cache_key == key, t.sources.c.expires_at > utcnow())
            return (await s.execute(query)).scalar_one_or_none()

    async def put(self, key: str, *, kind: str, url: str, payload: dict[str, Any], ttl_s: int) -> None:
        now = utcnow()
        values = {"cache_key": key, "kind": kind, "url": url, "payload": payload, "fetched_at": now,
                  "expires_at": now + timedelta(seconds=ttl_s)}  # fmt: skip
        stmt = insert(t.sources).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["cache_key"], set_={k: stmt.excluded[k] for k in values if k != "cache_key"})
        async with self._session_factory() as s:
            await s.execute(stmt)
            await s.commit()


def _conversation(r: Any) -> Conversation:
    return Conversation(id=r.id, title=r.title, created_at=r.created_at, updated_at=r.updated_at, metadata=r.metadata)


def _artifact(r: Any) -> Artifact:
    return Artifact(
        id=r.id, conversation_id=r.conversation_id, turn_id=r.turn_id, filename=r.filename, media_type=r.media_type,
        storage_uri=r.storage_uri, size_bytes=r.size_bytes, sha256=r.sha256, created_at=r.created_at,
    )  # fmt: skip


def _turn_values(turn: Turn) -> dict[str, Any]:
    return {
        "id": turn.id, "conversation_id": turn.conversation_id, "status": turn.status.value,
        "explicit_export": turn.explicit_export, "active_skill": turn.active_skill,
        "model_calls": turn.usage.model_calls, "tool_calls": turn.usage.tool_calls,
        "tokens_in": turn.usage.tokens_in, "tokens_out": turn.usage.tokens_out, "model": turn.usage.model,
        "started_at": turn.started_at, "finished_at": turn.finished_at, "error": turn.error, "metadata": turn.metadata,
    }  # fmt: skip
