from __future__ import annotations

from collections.abc import Iterable

from agent_harness.harness.tools.spec import ToolSpec


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool '{spec.name}' is already registered")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def names(self) -> set[str]:
        return set(self._specs)

    def with_tag(self, tag: str) -> set[str]:
        return {name for name, spec in self._specs.items() if tag in spec.tags}
