"""Rule R2: files are written only when the user explicitly asked in the current turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_harness.domain.conversation import Intent

ExportMode = Literal["explicit", "deny", "allow"]


@dataclass(frozen=True, slots=True)
class ExportPolicy:
    mode: ExportMode = "explicit"

    def allows(self, intent: Intent) -> bool:
        if self.mode == "deny":
            return False
        if self.mode == "allow":
            return True
        return intent.explicit_export
