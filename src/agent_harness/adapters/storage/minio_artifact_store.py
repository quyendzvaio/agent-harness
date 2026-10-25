from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import AsyncIterator

from minio import Minio
from minio.error import S3Error

from agent_harness.domain.errors import NotFound
from agent_harness.ports.artifact_store import StoredObject

_CHUNK = 64 * 1024


class MinioArtifactStore:
    """S3-compatible store on self-hosted MinIO. The SDK is synchronous, so calls run in a thread."""

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self.bucket = bucket

    @classmethod
    def connect(cls, endpoint: str, access_key: str, secret_key: str, bucket: str, *, secure: bool = False) -> MinioArtifactStore:
        return cls(Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure), bucket)

    async def ensure_bucket(self) -> None:
        def ensure() -> None:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)

        await asyncio.to_thread(ensure)

    async def put(self, key: str, data: bytes, media_type: str) -> StoredObject:
        await asyncio.to_thread(
            self._client.put_object, self.bucket, key, io.BytesIO(data), len(data), content_type=media_type
        )
        return StoredObject(f"s3://{self.bucket}/{key}", hashlib.sha256(data).hexdigest(), len(data))

    async def open(self, key: str) -> AsyncIterator[bytes]:
        try:
            response = await asyncio.to_thread(self._client.get_object, self.bucket, key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchBucket"):
                raise NotFound(f"object {key} not found") from exc
            raise
        try:
            while chunk := await asyncio.to_thread(response.read, _CHUNK):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self.bucket, key)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._client.stat_object, self.bucket, key)
            return True
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchBucket", "NoSuchObject"):
                return False
            raise

    async def health(self) -> None:
        if not await asyncio.to_thread(self._client.bucket_exists, self.bucket):
            raise RuntimeError(f"bucket '{self.bucket}' does not exist; run `agent-harness init`")

    async def remove_bucket(self) -> None:
        """Test helper: delete every object, then the bucket."""

        def remove() -> None:
            for obj in self._client.list_objects(self.bucket, recursive=True):
                self._client.remove_object(self.bucket, obj.object_name)
            self._client.remove_bucket(self.bucket)

        await asyncio.to_thread(remove)
