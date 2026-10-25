"""Optional Python hooks of trusted skills (rule R7).

A skill's `hooks.py` may define any of:
    on_load(skill_name: str) -> str | None        extra instructions appended after loading
    post_tool(tool: str, content: str) -> str     rewrite a tool's output before the model sees it
    on_final(text: str) -> str                    rewrite the final answer before it is stored

Hooks receive and return plain data only. Errors and timeouts are logged and ignored.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from types import ModuleType
from typing import Any

from agent_harness.domain.skills import SkillManifest
from agent_harness.harness.observability.logger import get_logger
from agent_harness.skills.parser import HOOKS_FILE

log = get_logger(__name__)

HOOK_NAMES = ("on_load", "post_tool", "on_final")


class HookRunner:
    def __init__(self, timeout_s: float = 2.0) -> None:
        self._timeout_s = timeout_s
        self._modules: dict[tuple[str, float], ModuleType] = {}

    async def run(self, skill: SkillManifest, hook: str, *args: Any) -> Any | None:
        if hook not in HOOK_NAMES or not skill.hooks_enabled:
            return None
        try:
            function = getattr(self._module(skill), hook, None)
            if function is None:
                return None
            if inspect.iscoroutinefunction(function):
                call = function(*args)
            else:
                call = asyncio.to_thread(function, *args)
            return await asyncio.wait_for(call, timeout=self._timeout_s)
        except Exception as exc:  # noqa: BLE001 - a broken hook must never break the turn
            log.warning("skill_hook_failed", extra={"skill": skill.name, "hook": hook, "error": repr(exc)})
            return None

    def _module(self, skill: SkillManifest) -> ModuleType:
        assert skill.root is not None
        path = skill.root / HOOKS_FILE
        key = (skill.name, path.stat().st_mtime)
        if key not in self._modules:
            spec = importlib.util.spec_from_file_location(f"agent_harness_skill_hooks.{skill.name.replace('-', '_')}", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._modules[key] = module
        return self._modules[key]
