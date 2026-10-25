from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field

from agent_harness.domain.events import EventType
from agent_harness.harness.context import RunContext
from agent_harness.harness.tools.spec import EXPORT, ToolOutput, ToolSpec
from agent_harness.ports.artifact_store import ArtifactSaver


class ExportInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=500_000, description="Full Markdown report, keep [E#] citations")


def slugify(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title.replace("đ", "d").replace("Đ", "D")).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_title).strip("-").lower()
    return (slug or "report")[:80]


def build_export_tools(saver: ArtifactSaver) -> list[ToolSpec]:
    async def export_markdown(args: ExportInput, ctx: RunContext) -> ToolOutput:
        artifact = await saver.save(
            conversation_id=ctx.conversation_id,
            turn_id=ctx.turn_id,
            filename=f"{slugify(args.title)}.md",
            content=args.content,
            media_type="text/markdown; charset=utf-8",
        )
        info = {"id": str(artifact.id), "filename": artifact.filename, "size_bytes": artifact.size_bytes}
        ctx.artifacts.append(info)
        ctx.emit(EventType.ARTIFACT, **info)
        return ToolOutput(f"Created file {artifact.filename} (artifact id {artifact.id}).", data=info)

    return [
        ToolSpec("export_markdown", "Save the report as a Markdown file. Only available when the user asked for a file.", ExportInput, export_markdown, frozenset({EXPORT})),
    ]  # fmt: skip
