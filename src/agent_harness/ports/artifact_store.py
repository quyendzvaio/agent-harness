from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from agent_harness.domain.conversation import Artifact


@dataclass(frozen=True, slots=True)
class StoredObject:
    uri: str
    sha256: str
    size_bytes: int


class ArtifactStore(Protocol):
    """Binary object storage. Implementations: MinIO (production), in-memory (tests)."""

    async def put(self, key: str, data: bytes, media_type: str) -> StoredObject: ...
    def open(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def health(self) -> None: ...


class ArtifactSaver(Protocol):
    """Stores an artifact's bytes and its metadata as one logical operation."""

    async def save(
        self, *, conversation_id: UUID, turn_id: UUID, filename: str, content: str, media_type: str
    ) -> Artifact: ...
