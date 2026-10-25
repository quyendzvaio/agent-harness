from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from agent_harness.api.auth import require_user
from agent_harness.api.deps import chat_service
from agent_harness.api.schemas import ChatRequest
from agent_harness.api.sse import format_event, sse_stream
from agent_harness.application.chat_service import ChatService

router = APIRouter(prefix="/v1", tags=["chat"], dependencies=[Depends(require_user)])


@router.post("/chat", response_class=StreamingResponse)
async def chat(body: ChatRequest, chat: ChatService = Depends(chat_service)) -> StreamingResponse:
    """One chat turn streamed as Server-Sent Events (see PRD §11 for event types)."""
    events = chat.stream_turn(body.message, body.conversation_id)
    # Pull the first event now so a missing conversation is a 404, not a broken stream.
    first = await anext(events)

    async def body_stream() -> AsyncIterator[str]:
        yield format_event(first)
        async for chunk in sse_stream(events):
            yield chunk

    return StreamingResponse(
        body_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
