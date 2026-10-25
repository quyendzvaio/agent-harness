from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from uuid import UUID

from agent_harness.application.intent_parser import parse_intent
from agent_harness.domain.conversation import Conversation, Message, Role, Turn, TurnStatus, utcnow
from agent_harness.domain.errors import NotFound
from agent_harness.domain.events import AgentEvent, EventType
from agent_harness.domain.policies import ExecutionLimits, ExportPolicy, SourcePolicy
from agent_harness.harness.context import RunContext
from agent_harness.harness.evidence_store import EvidenceStore
from agent_harness.harness.observability.logger import get_logger
from agent_harness.harness.runtime import AgentRuntime
from agent_harness.ports.repositories import UnitOfWorkFactory

log = get_logger(__name__)


class ChatService:
    """One chat turn: persist input, run the harness runtime, persist the outcome."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        runtime: AgentRuntime,
        limits: ExecutionLimits,
        limit_ceiling: ExecutionLimits | None = None,
        export_policy: ExportPolicy,
        source_policy: SourcePolicy,
        system_prompt_builder: Callable[[RunContext], str] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._runtime = runtime
        self._limits = limits
        self._limit_ceiling = limit_ceiling
        self._export_policy = export_policy
        self._source_policy = source_policy
        self._prompt_builder = system_prompt_builder
        self._background: set[asyncio.Task[None]] = set()

    async def stream_turn(self, message: str, conversation_id: UUID | None = None) -> AsyncIterator[AgentEvent]:
        intent = parse_intent(message)
        async with self._uow_factory() as uow:
            if conversation_id is None:
                conversation = Conversation(title=message.strip().splitlines()[0][:80] if message.strip() else "")
                await uow.conversations.add(conversation)
            else:
                found = await uow.conversations.get(conversation_id)
                if found is None:
                    raise NotFound(f"conversation {conversation_id} not found")
                conversation = found
            turn = Turn(conversation_id=conversation.id, explicit_export=intent.explicit_export)
            await uow.turns.add(turn)
            await uow.messages.add(Message(conversation.id, Role.USER, message, turn_id=turn.id))
            prior_evidence = await uow.evidence.list(conversation.id)
            await uow.commit()

        ctx = RunContext(
            conversation_id=conversation.id,
            turn_id=turn.id,
            message=message,
            intent=intent,
            limits=self._limits,
            limit_ceiling=self._limit_ceiling,
            export_policy=self._export_policy,
            source_policy=self._source_policy,
            evidence=EvidenceStore(prior_evidence),
            system_prompt_builder=self._prompt_builder,
        )
        yield AgentEvent(EventType.CONVERSATION, {"conversation_id": str(conversation.id), "turn_id": str(turn.id)})

        persisted = False
        try:
            async for event in self._runtime.stream(ctx):
                if event.terminal:
                    # Persist before the client sees done/error, so a follow-up GET sees the turn.
                    status = TurnStatus.COMPLETED if event.type is EventType.DONE else TurnStatus.FAILED
                    await self._persist(ctx, turn, status)
                    persisted = True
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            if persisted:
                raise
            # Client went away. Persist in a task the request's cancel scope cannot reach.
            task = asyncio.get_running_loop().create_task(self._persist(ctx, turn, TurnStatus.CANCELLED))
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            raise

    async def drain(self) -> None:
        """Wait for background persistence (tests, graceful shutdown)."""
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    async def _persist(self, ctx: RunContext, turn: Turn, status: TurnStatus) -> None:
        turn.status = status
        turn.usage = ctx.usage
        turn.active_skill = ctx.active_skill.name if ctx.active_skill else None
        turn.finished_at = utcnow()
        turn.error = ctx.fatal.to_dict() if ctx.fatal and status is not TurnStatus.COMPLETED else None
        turn.metadata = {"artifacts": ctx.artifacts} if ctx.artifacts else {}
        try:
            async with self._uow_factory() as uow:
                if ctx.final_text:
                    await uow.messages.add(
                        Message(
                            ctx.conversation_id,
                            Role.ASSISTANT,
                            ctx.final_text,
                            turn_id=ctx.turn_id,
                            metadata={"evidence_labels": sorted(ctx.evidence.labels())},
                        )
                    )
                await uow.evidence.add_many(ctx.conversation_id, ctx.turn_id, ctx.evidence.new_items)
                await uow.tool_invocations.add_many(ctx.tool_invocations)
                await uow.turns.update(turn)
                await uow.conversations.touch(ctx.conversation_id)
                await uow.commit()
        except Exception:
            log.exception("turn_persist_failed", extra={"turn_id": str(turn.id)})
