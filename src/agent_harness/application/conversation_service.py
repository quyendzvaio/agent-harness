from __future__ import annotations

from uuid import UUID

from agent_harness.domain.conversation import Conversation, Message
from agent_harness.domain.errors import NotFound
from agent_harness.domain.evidence import Evidence
from agent_harness.ports.repositories import UnitOfWorkFactory


class ConversationService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[Conversation]:
        async with self._uow_factory() as uow:
            return await uow.conversations.list(limit=limit, offset=offset)

    async def get(self, conversation_id: UUID) -> Conversation:
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(conversation_id)
        if conversation is None:
            raise NotFound(f"conversation {conversation_id} not found")
        return conversation

    async def messages(self, conversation_id: UUID) -> list[Message]:
        await self.get(conversation_id)
        async with self._uow_factory() as uow:
            return await uow.messages.list(conversation_id)

    async def evidence(self, conversation_id: UUID) -> list[Evidence]:
        await self.get(conversation_id)
        async with self._uow_factory() as uow:
            return await uow.evidence.list(conversation_id)
