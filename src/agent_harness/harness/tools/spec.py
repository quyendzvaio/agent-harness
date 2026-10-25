from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel

from agent_harness.domain.errors import HarnessError

if TYPE_CHECKING:
    from agent_harness.harness.context import RunContext

# Tags drive policy and scoping (rules R2, R4).
CORE = "core"  # always visible (load_skill, ...)
SOURCE = "source"  # reads an external source
EXPORT = "export"  # writes an artifact
DIAGRAM = "diagram"  # draws a diagram; only when the user asked for one


@dataclass(frozen=True, slots=True)
class ToolOutput:
    content: str
    evidence_labels: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[[Any, "RunContext"], Awaitable[ToolOutput | str]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    tags: frozenset[str] = frozenset()
    #: Name of the input field holding a URL that rule R1 must check before the handler runs.
    url_field: str | None = None
    #: Output comes from an external source and must be marked as untrusted data (rule R6).
    untrusted_output: bool = False

    def json_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()


@dataclass(slots=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    call_id: str = field(default_factory=lambda: uuid4().hex[:12])
    spec: ToolSpec | None = None
    #: Validated input, set by the validation middleware.
    parsed: BaseModel | None = None


@dataclass(slots=True)
class ToolResult:
    call_id: str
    tool: str
    ok: bool
    content: str
    error: HarnessError | None = None
    evidence_labels: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    @classmethod
    def failure(cls, call: ToolCall, error: HarnessError) -> ToolResult:
        return cls(call.call_id, call.name, False, f"ERROR [{error.code}]: {error.message}", error=error)

    @property
    def status(self) -> str:
        return "ok" if self.ok else (self.error.code if self.error else "error")
