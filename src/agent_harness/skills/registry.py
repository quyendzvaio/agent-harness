from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_harness.domain.errors import InvalidInput, NotFound
from agent_harness.domain.skills import SkillManifest, SkillTrust
from agent_harness.harness.observability.logger import get_logger
from agent_harness.skills.parser import SKILL_FILE, SkillParseError, load_skill_dir

log = get_logger(__name__)

BUILTIN_DIR = Path(__file__).parent / "builtin"


@dataclass(frozen=True, slots=True)
class SkillDirectory:
    path: Path
    trust: SkillTrust


class SkillRegistry:
    """File-backed skill catalog. Directories listed first win on name clashes (built-ins come first)."""

    def __init__(self, directories: Sequence[SkillDirectory]) -> None:
        self._directories = list(directories)
        self._skills: dict[str, SkillManifest] = {}
        self._lock = threading.Lock()
        self.problems: list[str] = []
        self.reload()

    def reload(self) -> list[str]:
        skills: dict[str, SkillManifest] = {}
        problems: list[str] = []
        for directory in self._directories:
            if not directory.path.is_dir():
                continue
            for child in sorted(p for p in directory.path.iterdir() if p.is_dir()):
                if not (child / SKILL_FILE).exists():
                    continue
                try:
                    manifest = load_skill_dir(child, directory.trust)
                except SkillParseError as exc:
                    problems.append(f"{child.name}: {exc}")
                    continue
                if manifest.name in skills:
                    problems.append(f"{child.name}: duplicate skill name '{manifest.name}' ignored")
                    continue
                skills[manifest.name] = manifest
        with self._lock:
            self._skills = skills
            self.problems = problems
        for problem in problems:
            log.warning("skill_skipped", extra={"problem": problem})
        return problems

    def list(self) -> list[SkillManifest]:
        with self._lock:
            return sorted(self._skills.values(), key=lambda s: s.name)

    def get(self, name: str) -> SkillManifest | None:
        with self._lock:
            return self._skills.get(name)

    def read_resource(self, name: str, path: str) -> str:
        skill = self.get(name)
        if skill is None or skill.root is None:
            raise NotFound(f"skill '{name}' does not exist")
        if path not in skill.resources:
            raise InvalidInput(f"'{path}' is not a declared resource of '{name}'", resources=list(skill.resources))
        root = skill.root.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise InvalidInput("resource path escapes the skill directory")
        return target.read_text(encoding="utf-8")
