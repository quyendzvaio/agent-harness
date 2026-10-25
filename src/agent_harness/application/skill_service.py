from __future__ import annotations

from agent_harness.domain.errors import NotFound
from agent_harness.domain.skills import SkillManifest
from agent_harness.ports.skill_source import ReloadableSkillCatalog


class SkillService:
    def __init__(self, catalog: ReloadableSkillCatalog) -> None:
        self._catalog = catalog

    def list(self) -> list[SkillManifest]:
        return self._catalog.list()

    def get(self, name: str) -> SkillManifest:
        skill = self._catalog.get(name)
        if skill is None:
            raise NotFound(f"skill '{name}' not found")
        return skill

    def reload(self) -> dict[str, object]:
        problems = self._catalog.reload()
        return {"skills": [s.name for s in self._catalog.list()], "problems": problems}
