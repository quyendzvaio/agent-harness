from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"


class SkillTrust(StrEnum):
    #: Shipped with the code base; may run Python hooks.
    TRUSTED = "trusted"
    #: Added by operators at runtime; instructions and resources only (rule R7).
    DECLARATIVE = "declarative"


class SkillLimits(BaseModel):
    """Budget a skill asks for (e.g. a deep repository read). Capped by the server's ceiling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_model_calls: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)
    turn_timeout_s: float | None = Field(default=None, gt=0)


class SkillManifest(BaseModel):
    """Parsed `SKILL.md`: frontmatter fields plus the instruction body."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=500)
    source_types: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    limits: SkillLimits | None = None
    instructions: str = ""
    trust: SkillTrust = SkillTrust.DECLARATIVE
    root: Path | None = None
    has_hooks: bool = False

    @field_validator("resources")
    @classmethod
    def _relative_resources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if Path(item).is_absolute() or ".." in Path(item).parts:
                raise ValueError(f"resource must be a relative path inside the skill: {item}")
        return value

    @property
    def hooks_enabled(self) -> bool:
        return self.has_hooks and self.trust is SkillTrust.TRUSTED

    def summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "source_types": list(self.source_types),
            "allowed_tools": list(self.allowed_tools),
            "resources": list(self.resources),
            "trust": self.trust.value,
            "limits": self.limits.model_dump(exclude_none=True) if self.limits else None,
            "hooks_enabled": self.hooks_enabled,
        }
