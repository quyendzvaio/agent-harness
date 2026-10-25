from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from agent_harness.domain.errors import HarnessError, LimitExceeded, ModelError
from agent_harness.domain.events import AgentEvent, EventType
from agent_harness.harness.context import RunContext
from agent_harness.harness.engine import AgentEngine, Finalizer
from agent_harness.harness.observability.logger import get_logger

log = get_logger(__name__)


class AgentRuntime:
    """Lifecycle of one turn: run the engine under a timeout, finalize, and stream events.

    The stream always ends with exactly one `done` or `error` event.
    """

    def __init__(self, engine: AgentEngine, finalizers: Sequence[Finalizer] = ()) -> None:
        self.engine = engine
        self._finalizers = list(finalizers)

    async def stream(self, ctx: RunContext) -> AsyncIterator[AgentEvent]:
        task = asyncio.create_task(self._run(ctx))
        try:
            while True:
                event = await ctx.events.get()
                if event is None:
                    break
                yield event
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _run(self, ctx: RunContext) -> None:
        try:
            loop = asyncio.get_running_loop()
            ctx.started_at = loop.time()
            async with asyncio.timeout(ctx.limits.turn_timeout_s) as scope:
                ctx.timeout_scope = scope  # a skill may extend the deadline (RunContext.raise_limits)
                text = await self.engine.run(ctx)
            if ctx.fatal is not None:
                raise ctx.fatal
            for finalize in self._finalizers:
                text = await finalize(ctx, text)
            ctx.final_text = text
            ctx.emit(EventType.DONE, usage=ctx.usage.to_dict())
        except TimeoutError:
            self._fail(ctx, LimitExceeded(f"turn_timeout_s={ctx.limits.turn_timeout_s:g} reached"))
        except HarnessError as exc:
            self._fail(ctx, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - engine/model failures surface as model_error
            log.exception("turn_crashed", extra={"turn_id": str(ctx.turn_id)})
            self._fail(ctx, ModelError(f"{type(exc).__name__}: {exc}"))
        finally:
            ctx.events.put_nowait(None)

    @staticmethod
    def _fail(ctx: RunContext, error: HarnessError) -> None:
        ctx.fatal = error
        ctx.emit(EventType.ERROR, **error.to_dict())
