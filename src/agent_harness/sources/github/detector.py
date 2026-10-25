from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent_harness.domain.sources import SourceKind, SourceRef

_HOSTS = {"github.com", "www.github.com"}
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class GithubTarget:
    owner: str
    repo: str
    ref: str | None = None
    path: str = ""

    @property
    def source(self) -> SourceRef:
        return SourceRef(SourceKind.GITHUB, f"https://github.com/{self.owner}/{self.repo}", f"github:{self.owner}/{self.repo}".lower())


def parse_github_url(url: str) -> GithubTarget | None:
    """Accepts repo, /tree/<ref>/<path> and /blob/<ref>/<path> URLs. Returns None for anything else."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in _HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1].removesuffix(".git")
    if not (_NAME.match(owner) and _NAME.match(repo)):
        return None
    if len(segments) >= 4 and segments[2] in ("tree", "blob"):
        return GithubTarget(owner, repo, segments[3], "/".join(segments[4:]))
    return GithubTarget(owner, repo)


def is_github_url(url: str) -> bool:
    return parse_github_url(url) is not None
