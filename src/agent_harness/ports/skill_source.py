from __future__ import annotations

from typing import Protocol

from agent_harness.domain.skills import SkillManifest


class SkillCatalog(Protocol):
    def list(self) -> list[SkillManifest]: ...
    def get(self, name: str) -> SkillManifest | None: ...
    def read_resource(self, name: str, path: str) -> str: ...


class ReloadableSkillCatalog(SkillCatalog, Protocol):
    def reload(self) -> list[str]:
        """Re-scan skill directories. Returns the problems found (invalid skills are skipped)."""
        ...
