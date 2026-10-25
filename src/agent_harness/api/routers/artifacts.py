from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from agent_harness.api.auth import require_user
from agent_harness.api.deps import artifact_service
from agent_harness.application.artifact_service import ArtifactService

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"], dependencies=[Depends(require_user)])


@router.get("/{artifact_id}")
async def download(artifact_id: UUID, service: ArtifactService = Depends(artifact_service)) -> StreamingResponse:
    """Streams the file from object storage; the bucket itself stays private."""
    artifact = await service.get(artifact_id)
    chunks = service.open(artifact)
    first = await anext(chunks, b"")  # surfaces a missing object as 404 before headers are sent

    async def body():
        yield first
        async for chunk in chunks:
            yield chunk

    return StreamingResponse(
        body(),
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.filename)}",
            "Content-Length": str(artifact.size_bytes),
            "ETag": f'"{artifact.sha256}"',
        },
    )
