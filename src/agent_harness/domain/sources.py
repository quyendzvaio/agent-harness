from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceKind(StrEnum):
    GITHUB = "github"
    BLOG = "blog"


@dataclass(frozen=True, slots=True)
class SourceRef:
    kind: SourceKind
    url: str
    #: Stable identity used for caching, e.g. "github:owner/repo" or the normalized URL.
    canonical_id: str
