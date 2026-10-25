from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from agent_harness.api.auth import require_user
from agent_harness.api.deps import artifact_service, conversation_service
from agent_harness.api.schemas import ArtifactOut, ConversationOut, EvidenceOut, MessageOut
from agent_harness.application.artifact_service import ArtifactService
from agent_harness.application.conversation_service import ConversationService

router = APIRouter(prefix="/v1/conversations", tags=["conversations"], dependencies=[Depends(require_user)])


@router.get("")
async def list_conversations(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    service: ConversationService = Depends(conversation_service),
) -> list[ConversationOut]:  # fmt: skip
    return [ConversationOut.of(c) for c in await service.list(limit=limit, offset=offset)]


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: UUID, service: ConversationService = Depends(conversation_service)) -> ConversationOut:
    return ConversationOut.of(await service.get(conversation_id))


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: UUID, service: ConversationService = Depends(conversation_service)) -> list[MessageOut]:
    return [MessageOut.of(m) for m in await service.messages(conversation_id)]


@router.get("/{conversation_id}/evidence")
async def list_evidence(conversation_id: UUID, service: ConversationService = Depends(conversation_service)) -> list[EvidenceOut]:
    """What each `[E#]` in the answers points at: source, locator and the quoted text."""
    return [EvidenceOut.of(e) for e in await service.evidence(conversation_id)]


@router.get("/{conversation_id}/artifacts")
async def list_artifacts(
    conversation_id: UUID,
    conversations: ConversationService = Depends(conversation_service),
    artifacts: ArtifactService = Depends(artifact_service),
) -> list[ArtifactOut]:
    await conversations.get(conversation_id)
    return [ArtifactOut.of(a) for a in await artifacts.list(conversation_id)]
