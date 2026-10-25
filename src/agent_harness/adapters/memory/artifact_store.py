from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from agent_harness.domain.errors import NotFound
from agent_harness.ports.artifact_store import StoredObject


class MemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.healthy = True

    async def put(self, key: str, data: bytes, media_type: str) -> StoredObject:
        self._check()
        self.objects[key] = (data, media_type)
        return StoredObject(f"memory://bucket/{key}", hashlib.sha256(data).hexdigest(), len(data))

    async def open(self, key: str) -> AsyncIterator[bytes]:
        self._check()
        if key not in self.objects:
            raise NotFound(f"object {key} not found")
        data = self.objects[key][0]
        for offset in range(0, len(data), 64 * 1024):
            yield data[offset : offset + 64 * 1024]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        self._check()
        return key in self.objects

    async def health(self) -> None:
        self._check()

    def _check(self) -> None:
        if not self.healthy:
            raise ConnectionError("artifact store unavailable")
