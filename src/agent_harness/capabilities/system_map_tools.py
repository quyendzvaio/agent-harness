"""`github_system_map`: the declared runtime topology of a repository, every row backed by `file:line`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_harness.domain.errors import InvalidInput
from agent_harness.domain.events import EventType
from agent_harness.domain.sources import SourceKind
from agent_harness.harness.context import RunContext
from agent_harness.harness.tools.spec import SOURCE, ToolOutput, ToolSpec
from agent_harness.sources.github.detector import GithubTarget, parse_github_url
from agent_harness.sources.github.repository_reader import RepositoryReader
from agent_harness.sources.github.system_map import SystemMap
from agent_harness.sources.github.system_map.render import Cite, render
from agent_harness.sources.registry import SourceRegistry

#: Skills whose answer is the map's five parts: the tables are streamed into the answer by the tool itself, so the
#: model writes only what it verified and its assessment (re-typing ~6k characters of tables took a slow model minutes).
TABLES_IN_ANSWER = frozenset({"github-system-architecture"})
PREFIX_MESSAGE_ID = "system_map"
_IN_ANSWER = (
    "\n\n---\nĐÃ CHÈN VÀO CÂU TRẢ LỜI cho người dùng: dòng đầu + 4 phần trên (node, cạnh, sở hữu dữ liệu, khoảng "
    "trống), nguyên văn và kèm [E#]. KHÔNG chép lại bảng nào. Câu trả lời của bạn chỉ gồm: `## Kiểm chứng` (mỗi dòng "
    "đã đọc: node/cạnh/khoảng trống → xác nhận hay sửa, mức tin cậy đúng, [E#] của lần đọc) và `## Nhận định của agent`."
)

Section = Literal["overview", "processes", "channels", "stores", "env", "gaps", "hypotheses"]


class SystemMapInput(BaseModel):
    url: str = Field(description="GitHub repository URL, e.g. https://github.com/owner/repo")
    section: Section = Field(
        default="overview",
        description="overview = the five parts (header, nodes, edges grouped per pair, data ownership, Q9 gaps); "
        "processes / channels (every route) / stores / env (Q3 variable chains) / gaps / hypotheses (README claims) in full",
    )
    variant: str = Field(default="", max_length=80, description="Only this compose variant/profile or 'k8s', e.g. 'full'")
    process: str = Field(default="", max_length=120, description="Only rows touching this node, e.g. 'api'")
    limit: int = Field(default=60, ge=5, le=200, description="Rows per table")


class SystemMaps:
    """Shared by `github_system_map` and `architecture_diagram(source=system_map)`."""

    def __init__(self, sources: SourceRegistry, github: RepositoryReader) -> None:
        self._sources = sources
        self.github = github

    def target(self, url: str) -> GithubTarget:
        self._sources.require(SourceKind.GITHUB)
        target = parse_github_url(url)
        if target is None:
            raise InvalidInput(f"not a GitHub repository URL: {url}")
        return target

    async def get(self, url: str) -> tuple[GithubTarget, SystemMap]:
        target = self.target(url)
        return target, await self.github.system_map(target)


def citer(ctx: RunContext, target: GithubTarget, labels: list[str]) -> Cite:
    def cite(locator: str, quote: str) -> str:
        label = ctx.evidence.add(target.source, locator, quote).label
        if label not in labels:
            labels.append(label)
        return f"[{label}]"

    return cite


def build_system_map_tools(maps: SystemMaps) -> list[ToolSpec]:
    async def system_map(args: SystemMapInput, ctx: RunContext) -> ToolOutput:
        target, smap = await maps.get(args.url)
        labels: list[str] = []
        text = render(
            smap, citer(ctx, target, labels), section=args.section, variant=args.variant or None,
            process=args.process or None, limit=args.limit,
        )  # fmt: skip
        data = {"processes": len(smap.processes), "channels": len(smap.channels), "gaps": len(smap.gaps), "runtime": smap.has_runtime}
        skill = ctx.active_skill.name if ctx.active_skill else None
        if args.section == "overview" and skill in TABLES_IN_ANSWER and not ctx.answer_prefix:
            block = render(
                smap, citer(ctx, target, labels), section="overview", variant=args.variant or None,
                process=args.process or None, limit=args.limit, footer=False,
            )  # fmt: skip
            ctx.answer_prefix.append(block)
            ctx.emit(EventType.TOKEN, text=block + "\n\n", message_id=PREFIX_MESSAGE_ID)
            text += _IN_ANSWER
            data["in_answer"] = True
        return ToolOutput(text, tuple(labels), data=data)

    return [
        ToolSpec(
            "github_system_map",
            "Deterministic runtime topology of a GitHub repo, read from deployment manifests first (compose, k8s, "
            "skaffold, Dockerfile, Procfile, CI): which processes run and their role, how they connect (HTTP routes "
            "matched client↔server, gRPC, queue topics, DB/cache/storage, volume mounts) with separate match/address "
            "confidence, who writes/reads each store, and the closure gaps (Q9). Call it FIRST for questions about "
            "services, components, how they connect, or an architecture diagram; then read only the file:line of "
            "INFERRED rows and gaps to confirm them.",
            SystemMapInput,
            system_map,
            frozenset({SOURCE}),
            url_field="url",
            untrusted_output=True,
        )
    ]
