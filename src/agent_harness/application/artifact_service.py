from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from agent_harness.domain.conversation import Artifact
from agent_harness.domain.errors import NotFound
from agent_harness.harness.observability.logger import get_logger
from agent_harness.ports.artifact_store import ArtifactStore
from agent_harness.ports.repositories import UnitOfWorkFactory

log = get_logger(__name__)


class ArtifactService:
    """`ArtifactSaver` implementation: object first, then metadata; compensate on DB failure (FR-17)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, store: ArtifactStore) -> None:
        self._uow_factory = uow_factory
        self._store = store

    async def save(
        self, *, conversation_id: UUID, turn_id: UUID, filename: str, content: str, media_type: str
    ) -> Artifact:
        artifact_id = uuid4()
        key = f"conversations/{conversation_id}/{artifact_id}/{filename}"
        stored = await self._store.put(key, content.encode("utf-8"), media_type)
        artifact = Artifact(
            id=artifact_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            filename=filename,
            media_type=media_type,
            storage_uri=stored.uri,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
        try:
            async with self._uow_factory() as uow:
                await uow.artifacts.add(artifact)
                await uow.commit()
        except Exception:
            log.warning("artifact_metadata_failed_compensating", extra={"key": key})
            await self._store.delete(key)
            raise
        return artifact

    async def get(self, artifact_id: UUID) -> Artifact:
        async with self._uow_factory() as uow:
            artifact = await uow.artifacts.get(artifact_id)
        if artifact is None:
            raise NotFound(f"artifact {artifact_id} not found")
        return artifact

    async def list(self, conversation_id: UUID) -> list[Artifact]:
        async with self._uow_factory() as uow:
            return await uow.artifacts.list(conversation_id)

    def open(self, artifact: Artifact) -> AsyncIterator[bytes]:
        return self._store.open(artifact.storage_key)
