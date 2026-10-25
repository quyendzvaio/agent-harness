"""Rules R2 and R4: which tools the model may see and call in this turn.

The same function feeds both the model request (hide tools) and the policy guard (block calls),
so a model that calls a hidden tool anyway is still stopped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agent_harness.harness.tools.registry import ToolRegistry
from agent_harness.harness.tools.spec import CORE, DIAGRAM, EXPORT, SOURCE

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext


#: Model calls / tool calls left (after the current model call) at which a requested diagram is due.
DIAGRAM_DUE_MODEL_CALLS = 2
DIAGRAM_DUE_TOOL_CALLS = 3
#: Share of the turn timeout left at which a missing diagram is due / the answer must be written. A slow model
#: needs minutes to stream a long answer, so this starts well before the deadline.
DIAGRAM_DUE_TIME_SHARE = 0.5
ANSWER_DUE_TIME_SHARE = 0.35

WrapUp = Literal["diagram", "answer"] | None


def wrap_up(ctx: RunContext, *, in_flight: int = 0) -> WrapUp:
    """Near the end of the budget, steer the model to finish instead of being cut off mid-read.

    `answer`: the last model call, no tools (it must write the answer from the evidence it has).
    `diagram`: a requested diagram is still missing and the budget is almost gone: only that tool.
    `in_flight`: tool calls already counted in `usage` but not finished (the policy guard runs after the limit
    middleware has counted the call it is checking).
    """
    calls_left = ctx.limits.max_model_calls - ctx.usage.model_calls  # the current call is already counted
    tools_left = ctx.limits.max_tool_calls - (ctx.usage.tool_calls - in_flight)
    seconds_left = ctx.seconds_left()
    time_share = 1.0 if seconds_left is None else seconds_left / ctx.limits.turn_timeout_s
    diagram_due = ctx.intent.wants_diagram and not ctx.diagrams
    if calls_left <= 0 or tools_left <= 0:
        return "answer"
    if diagram_due and (
        calls_left <= DIAGRAM_DUE_MODEL_CALLS
        or tools_left <= DIAGRAM_DUE_TOOL_CALLS
        or time_share < DIAGRAM_DUE_TIME_SHARE
    ):
        return "diagram"
    if time_share < ANSWER_DUE_TIME_SHARE:
        return "answer"
    return None


def allowed_tool_names(ctx: RunContext, registry: ToolRegistry, *, in_flight: int = 0) -> set[str]:
    phase = wrap_up(ctx, in_flight=in_flight)
    if phase == "answer":
        return set()
    if phase == "diagram":
        return registry.with_tag(DIAGRAM)
    allowed = registry.with_tag(CORE)
    if ctx.active_skill is not None:
        allowed |= set(ctx.active_skill.allowed_tools) & registry.names()
    else:
        allowed |= registry.with_tag(SOURCE)
    # Export is governed only by the export policy (R2), so an active skill never hides it.
    if ctx.export_allowed:
        allowed |= registry.with_tag(EXPORT)
    else:
        allowed -= registry.with_tag(EXPORT)
    # Diagrams cost tokens and time: only when this message asks for one, whatever the skill.
    if ctx.intent.wants_diagram:
        allowed |= registry.with_tag(DIAGRAM)
    else:
        allowed -= registry.with_tag(DIAGRAM)
    return allowed
