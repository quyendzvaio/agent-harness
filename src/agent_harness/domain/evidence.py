from __future__ import annotations

import re
from dataclasses import dataclass

from agent_harness.domain.sources import SourceRef

CITATION_PATTERN = re.compile(r"\[(E\d+)\]")


@dataclass(frozen=True, slots=True)
class Evidence:
    #: Conversation-scoped label the model cites, e.g. "E3".
    label: str
    source: SourceRef
    #: Where in the source: "path/to/file.py:L10-L42", "tree:/src", "§Heading ¶2".
    locator: str
    quote: str


@dataclass(frozen=True, slots=True)
class CitationReport:
    valid: tuple[str, ...]
    invalid: tuple[str, ...]
    uncited_sentences: int
    total_sentences: int

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": list(self.valid),
            "invalid": list(self.invalid),
            "uncited_sentences": self.uncited_sentences,
            "total_sentences": self.total_sentences,
        }
