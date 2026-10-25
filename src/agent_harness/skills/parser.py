from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_harness.domain.skills import SkillManifest, SkillTrust

SKILL_FILE = "SKILL.md"
HOOKS_FILE = "hooks.py"


class SkillParseError(ValueError):
    pass


def split_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillParseError("SKILL.md must start with a '---' frontmatter block")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise SkillParseError("frontmatter block is not closed with '---'") from exc
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillParseError("frontmatter must be a mapping")
    return meta, "\n".join(lines[end + 1 :]).strip()


def parse_skill(text: str, *, root: Path | None = None, trust: SkillTrust = SkillTrust.DECLARATIVE) -> SkillManifest:
    meta, body = split_frontmatter(text)
    for key in ("source_types", "allowed_tools", "resources"):
        if isinstance(meta.get(key), str):
            meta[key] = [meta[key]]
    if root is not None and meta.get("name") and meta["name"] != root.name:
        raise SkillParseError(f"skill name '{meta['name']}' must match its directory '{root.name}'")
    try:
        return SkillManifest(
            **meta,
            instructions=body,
            trust=trust,
            root=root,
            has_hooks=root is not None and (root / HOOKS_FILE).is_file(),
        )
    except (ValidationError, TypeError) as exc:
        raise SkillParseError(str(exc)) from exc


def load_skill_dir(path: Path, trust: SkillTrust) -> SkillManifest:
    skill_file = path / SKILL_FILE
    if not skill_file.is_file():
        raise SkillParseError(f"{path} has no {SKILL_FILE}")
    manifest = parse_skill(skill_file.read_text(encoding="utf-8"), root=path, trust=trust)
    for resource in manifest.resources:
        if not (path / resource).is_file():
            raise SkillParseError(f"resource '{resource}' declared by '{manifest.name}' does not exist")
    return manifest
