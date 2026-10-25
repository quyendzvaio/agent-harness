"""Rule R8: a disabled source fails clearly without affecting the others."""

from __future__ import annotations

from agent_harness.domain.errors import CapabilityUnavailable
from agent_harness.domain.sources import SourceKind


class SourceRegistry:
    def __init__(self, enabled: frozenset[str]) -> None:
        unknown = enabled - {kind.value for kind in SourceKind}
        if unknown:
            raise ValueError(f"unknown source kinds: {sorted(unknown)}")
        self._enabled = enabled

    def is_enabled(self, kind: SourceKind) -> bool:
        return kind.value in self._enabled

    def require(self, kind: SourceKind) -> None:
        if not self.is_enabled(kind):
            raise CapabilityUnavailable(f"source '{kind.value}' is disabled on this server", source=kind.value)

    @property
    def enabled(self) -> list[str]:
        return sorted(self._enabled)
