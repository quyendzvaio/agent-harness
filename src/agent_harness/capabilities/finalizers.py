"""Glue between harness extension points and research/skill logic."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agent_harness.domain.events import EventType
from agent_harness.domain.policies import CitationPolicy
from agent_harness.harness.context import RunContext
from agent_harness.harness.middleware.base import NextTool
from agent_harness.harness.middleware.tool_scope import wrap_up
from agent_harness.harness.tools.spec import ToolCall, ToolResult
from agent_harness.harness.observability.logger import get_logger
from agent_harness.ports.claim_verifier import Claim, ClaimVerifier
from agent_harness.ports.skill_source import SkillCatalog
from agent_harness.research.citation_validator import cited_sentences, validate_citations
from agent_harness.research.prompting import build_system_prompt
from agent_harness.skills.hooks import HookRunner


log = get_logger(__name__)


class CitationFinalizer:
    """Rule R5: report valid/invalid citations of the final answer.

    With a `ClaimVerifier`, also checks that each cited sentence is supported by its evidence. A
    verifier failure or timeout never fails the turn; the report then simply has no `claims` key.
    """

    def __init__(
        self, policy: CitationPolicy | None = None, verifier: ClaimVerifier | None = None, *, timeout_s: float = 30.0,
        max_claims: int = 40,
    ) -> None:  # fmt: skip
        self._policy = policy or CitationPolicy()
        self._verifier = verifier
        self._timeout_s = timeout_s
        self._max_claims = max_claims

    async def __call__(self, ctx: RunContext, text: str) -> str:
        report = validate_citations(text, ctx.evidence.labels(), self._policy).to_dict()
        if self._verifier is not None:
            claims = [
                Claim(sentence, tuple(e for label in labels if (e := ctx.evidence.get(label))))
                for sentence, labels in cited_sentences(text, self._policy)
            ]
            claims = [c for c in claims if c.evidence][: self._max_claims]
            if claims:
                try:
                    verdicts = await asyncio.wait_for(self._verifier.verify(claims), self._timeout_s)
                    report["claims"] = {
                        "checked": len(verdicts),
                        **{k: sum(v.verdict == k for v in verdicts) for k in ("supported", "unsupported", "uncertain")},
                        "flagged": [
                            {"sentence": v.claim.sentence, "labels": [e.label for e in v.claim.evidence],
                             "verdict": v.verdict, "probability": v.probability, "note": v.note}  # fmt: skip
                            for v in verdicts
                            if v.verdict != "supported"
                        ][:20],
                    }
                except Exception as exc:  # noqa: BLE001 - verification is best effort
                    log.warning("claim_verification_failed", extra={"error": repr(exc), "turn_id": str(ctx.turn_id)})
        ctx.emit(EventType.CITATION_REPORT, **report)
        return text


class AnswerPrefixFinalizer:
    """Markdown a tool streamed ahead of the model's answer (`ctx.answer_prefix`) stays at its start once the
    answer is persisted, so a reload shows what was streamed. Skipped when the model copied it anyway."""

    async def __call__(self, ctx: RunContext, text: str) -> str:
        # A heading the model may write itself does not identify a block; its longest line (a table row) does.
        blocks = [block for block in ctx.answer_prefix if max(block.splitlines(), key=len) not in text]
        return "\n\n".join([*blocks, text]) if blocks else text


class DiagramFinalizer:
    """Diagrams drawn with `architecture_diagram` always reach the answer: a block the model did not copy
    is appended (and streamed). Runs before `CitationFinalizer` so the step table's citations count."""

    async def __call__(self, ctx: RunContext, text: str) -> str:
        missing = [block for block in ctx.diagrams if not _contains_diagram(text, block)]
        if not missing:
            return text
        heading = "Sơ đồ kiến trúc" if ctx.intent.language == "vi" else "Architecture diagram"
        appended = "".join(f"\n\n## {heading}\n\n{block}" for block in missing)
        ctx.emit(EventType.TOKEN, text=appended, message_id="diagram")
        return text + appended


def _contains_diagram(text: str, block: str) -> bool:
    """The model may reflow the table; the mermaid code itself (edges included) must be there."""
    code = block.split("```mermaid", 1)[-1].split("```", 1)[0]
    lines = [line.strip() for line in code.splitlines() if "--" in line or line.strip().startswith("flowchart")]
    normalized = "\n".join(line.strip() for line in text.splitlines())
    return bool(lines) and all(line in normalized for line in lines)


class SkillFinalHook:
    def __init__(self, hooks: HookRunner) -> None:
        self._hooks = hooks

    async def __call__(self, ctx: RunContext, text: str) -> str:
        if ctx.active_skill is None:
            return text
        result = await self._hooks.run(ctx.active_skill, "on_final", text)
        return result if isinstance(result, str) else text


class SkillPostToolHook:
    """Tool middleware running the active skill's `post_tool` hook on successful results."""

    def __init__(self, hooks: HookRunner) -> None:
        self._hooks = hooks

    async def __call__(self, call: ToolCall, ctx: RunContext, next: NextTool) -> ToolResult:
        result = await next(call, ctx)
        if result.ok and ctx.active_skill is not None:
            rewritten = await self._hooks.run(ctx.active_skill, "post_tool", call.name, result.content)
            if isinstance(rewritten, str):
                result.content = rewritten
        return result


def system_prompt_builder(catalog: SkillCatalog) -> Callable[[RunContext], str]:
    def build(ctx: RunContext) -> str:
        return build_system_prompt(
            skills=catalog.list(),
            active_skill=ctx.active_skill,
            language=ctx.intent.language,
            export_allowed=ctx.export_allowed,
            wants_diagram=ctx.intent.wants_diagram,
            notes=ctx.notes,
            wrap_up=wrap_up(ctx),
        )

    return build
