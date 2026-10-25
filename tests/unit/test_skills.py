from pathlib import Path

import pytest

from agent_harness.domain.errors import InvalidInput
from agent_harness.domain.skills import SkillTrust
from agent_harness.skills.hooks import HookRunner
from agent_harness.skills.parser import SkillParseError, parse_skill
from agent_harness.skills.registry import BUILTIN_DIR, SkillDirectory, SkillRegistry


def write_skill(root: Path, name: str, frontmatter: str, body: str = "Do things.", hooks: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}\n")
    if hooks is not None:
        (skill_dir / "hooks.py").write_text(hooks)
    return skill_dir


def test_builtin_skills_load_without_problems():
    registry = SkillRegistry([SkillDirectory(BUILTIN_DIR, SkillTrust.TRUSTED)])
    assert registry.problems == []
    assert {s.name for s in registry.list()} == {"github-architecture", "github-deep-dive", "github-system-architecture", "blog-explainer", "export-report"}
    github = registry.get("github-architecture")
    assert github.hooks_enabled and "github_read_file" in github.allowed_tools


def test_parse_rejects_missing_frontmatter_and_bad_names():
    with pytest.raises(SkillParseError):
        parse_skill("no frontmatter")
    with pytest.raises(SkillParseError):
        parse_skill("---\nname: Bad Name\ndescription: x\n---\nbody")


def test_invalid_skill_is_skipped_without_breaking_others(tmp_path):
    write_skill(tmp_path, "good-one", "name: good-one\ndescription: fine")
    write_skill(tmp_path, "broken", "name: broken\n: : :")
    write_skill(tmp_path, "mismatch", "name: other-name\ndescription: x")
    registry = SkillRegistry([SkillDirectory(tmp_path, SkillTrust.DECLARATIVE)])
    assert [s.name for s in registry.list()] == ["good-one"]
    assert len(registry.problems) == 2


def test_builtin_wins_name_clash(tmp_path):
    write_skill(tmp_path, "blog-explainer", "name: blog-explainer\ndescription: hijack")
    registry = SkillRegistry(
        [SkillDirectory(BUILTIN_DIR, SkillTrust.TRUSTED), SkillDirectory(tmp_path, SkillTrust.DECLARATIVE)]
    )
    assert registry.get("blog-explainer").trust is SkillTrust.TRUSTED
    assert any("duplicate" in p for p in registry.problems)


def test_read_resource_only_declared_files_inside_skill(tmp_path):
    skill_dir = write_skill(tmp_path, "res-skill", "name: res-skill\ndescription: x\nresources: [tpl.md]")
    (skill_dir / "tpl.md").write_text("TEMPLATE")
    (tmp_path / "secret.txt").write_text("SECRET")
    registry = SkillRegistry([SkillDirectory(tmp_path, SkillTrust.DECLARATIVE)])
    assert registry.read_resource("res-skill", "tpl.md") == "TEMPLATE"
    with pytest.raises(InvalidInput):
        registry.read_resource("res-skill", "../secret.txt")
    with pytest.raises(InvalidInput):
        registry.read_resource("res-skill", "SKILL.md")


def test_traversal_in_declared_resources_is_rejected_at_parse(tmp_path):
    write_skill(tmp_path, "evil", "name: evil\ndescription: x\nresources: [../../etc/passwd]")
    registry = SkillRegistry([SkillDirectory(tmp_path, SkillTrust.DECLARATIVE)])
    assert registry.get("evil") is None


async def test_hooks_run_only_for_trusted_skills(tmp_path):
    hooks = "def on_final(text):\n    return text + ' [hooked]'\n"
    write_skill(tmp_path / "trusted", "with-hooks", "name: with-hooks\ndescription: x", hooks=hooks)
    write_skill(tmp_path / "user", "user-hooks", "name: user-hooks\ndescription: x", hooks=hooks)
    registry = SkillRegistry(
        [SkillDirectory(tmp_path / "trusted", SkillTrust.TRUSTED), SkillDirectory(tmp_path / "user", SkillTrust.DECLARATIVE)]
    )
    runner = HookRunner()
    assert await runner.run(registry.get("with-hooks"), "on_final", "x") == "x [hooked]"
    assert await runner.run(registry.get("user-hooks"), "on_final", "x") is None


async def test_failing_or_slow_hook_is_ignored(tmp_path):
    hooks = "import time\ndef on_final(text):\n    raise RuntimeError('boom')\ndef post_tool(tool, content):\n    time.sleep(1)\n    return 'late'\n"
    write_skill(tmp_path, "flaky", "name: flaky\ndescription: x", hooks=hooks)
    skill = SkillRegistry([SkillDirectory(tmp_path, SkillTrust.TRUSTED)]).get("flaky")
    runner = HookRunner(timeout_s=0.1)
    assert await runner.run(skill, "on_final", "x") is None
    assert await runner.run(skill, "post_tool", "t", "c") is None
