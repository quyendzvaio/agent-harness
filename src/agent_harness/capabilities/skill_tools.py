from __future__ import annotations

from pydantic import BaseModel, Field

from agent_harness.domain.errors import InvalidInput
from agent_harness.domain.events import EventType
from agent_harness.harness.context import RunContext
from agent_harness.harness.tools.spec import CORE, ToolSpec
from agent_harness.ports.skill_source import SkillCatalog
from agent_harness.skills.hooks import HookRunner


class LoadSkillInput(BaseModel):
    name: str = Field(description="Skill name from the catalog in the system prompt")


class NoteInput(BaseModel):
    topic: str = Field(min_length=1, max_length=120, description="e.g. a module or directory name")
    text: str = Field(min_length=1, max_length=2_000, description="Findings, keep [E#] citations")


class SkillResourceInput(BaseModel):
    name: str
    path: str = Field(description="Resource path listed by load_skill, e.g. templates/report.md")


def build_skill_tools(catalog: SkillCatalog, hooks: HookRunner) -> list[ToolSpec]:
    async def load_skill(args: LoadSkillInput, ctx: RunContext) -> str:
        skill = catalog.get(args.name)
        if skill is None:
            names = ", ".join(s.name for s in catalog.list())
            raise InvalidInput(f"unknown skill '{args.name}'. Available: {names}")
        ctx.active_skill = skill
        raised = ctx.raise_limits(skill.limits)
        ctx.emit(EventType.SKILL_LOADED, name=skill.name, **({"limits": raised} if raised else {}))
        parts = [f"# Skill loaded: {skill.name}", skill.instructions]
        if raised or skill.limits:
            budget = ctx.limits
            parts.append(f"Budget for this turn: {budget.max_model_calls} model calls, {budget.max_tool_calls} tool calls, "
                         f"{budget.turn_timeout_s:g}s. Used so far: {ctx.usage.model_calls} model / {ctx.usage.tool_calls} tool calls.")  # fmt: skip
        if skill.allowed_tools:
            parts.append("Tools available with this skill: " + ", ".join(skill.allowed_tools))
        if skill.resources:
            parts.append("Resources (read with read_skill_resource): " + ", ".join(skill.resources))
        extra = await hooks.run(skill, "on_load", skill.name)
        if isinstance(extra, str) and extra.strip():
            parts.append(extra)
        return "\n\n".join(parts)

    async def read_resource(args: SkillResourceInput, ctx: RunContext) -> str:
        return catalog.read_resource(args.name, args.path)

    async def save_note(args: NoteInput, ctx: RunContext) -> str:
        for note in ctx.notes:
            if note["topic"] == args.topic:
                note["text"] = args.text
                break
        else:
            ctx.notes.append({"topic": args.topic, "text": args.text})
        return f"Saved note '{args.topic}' ({len(ctx.notes)} notes). Notes stay in your system prompt for the rest of the turn."

    return [
        ToolSpec("save_note", "Save working notes (findings per module, with [E#]). Notes stay visible even after old tool outputs are cleared from context.", NoteInput, save_note, frozenset({CORE})),
        ToolSpec("load_skill", "Load a skill's full instructions. Do this before analysing a source.", LoadSkillInput, load_skill, frozenset({CORE})),
        ToolSpec("read_skill_resource", "Read a template/resource file that belongs to a skill.", SkillResourceInput, read_resource, frozenset({CORE})),
    ]  # fmt: skip
