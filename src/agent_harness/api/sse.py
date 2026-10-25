from __future__ import annotations

import json
from collections.abc import AsyncIterator

from agent_harness.domain.errors import HarnessError
from agent_harness.domain.events import AgentEvent, EventType


def format_event(event: AgentEvent) -> str:
    return f"event: {event.type.value}\ndata: {json.dumps(event.data, ensure_ascii=False, default=str)}\n\n"


async def sse_stream(events: AsyncIterator[AgentEvent]) -> AsyncIterator[str]:
    """Serialize events; an error raised before the stream ends still becomes a final `error` event."""
    try:
        async for event in events:
            yield format_event(event)
    except HarnessError as exc:
        yield format_event(AgentEvent(EventType.ERROR, exc.to_dict()))
