"""Test doubles shared by unit and integration tests."""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_harness.domain.conversation import Intent
from agent_harness.domain.errors import InvalidInput
from agent_harness.domain.policies import ExecutionLimits, ExportPolicy, SourcePolicy
from agent_harness.harness.context import RunContext
from agent_harness.ports.source_fetcher import FetchResponse

FIXTURES = Path(__file__).parent / "fixtures"
REPO_URL = "https://github.com/acme/tiny-api"
BLOG_URL = "https://blog.example.com/posts/event-sourcing"


def load_repo_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "sample_repository.json").read_text())


def blog_html() -> str:
    return (FIXTURES / "sample_blog.html").read_text()


class FakeGithubClient:
    """Stands in for `GithubClient`; nothing above it changes when it is swapped (scenario S4)."""

    def __init__(self, fixture: dict[str, Any] | None = None) -> None:
        self.fixture = fixture or load_repo_fixture()
        self.calls: list[str] = []

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        self.calls.append(f"repo {owner}/{repo}")
        self._check(owner, repo)
        return self.fixture["meta"]

    async def get_tree(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:
        self.calls.append(f"tree {ref}")
        self._check(owner, repo)
        files = self.fixture["files"]
        dirs = {"/".join(p.split("/")[:i]) for p in files for i in range(1, p.count("/") + 1)}
        return [{"path": d, "type": "tree", "size": None} for d in sorted(dirs)] + [
            {"path": p, "type": "blob", "size": len(c)} for p, c in files.items()
        ]

    async def get_file(self, owner: str, repo: str, path: str, ref: str | None = None) -> str:
        self.calls.append(f"file {path}")
        self._check(owner, repo)
        if path not in self.fixture["files"]:
            raise InvalidInput(f"GitHub resource not found (or private): {path}")
        return self.fixture["files"][path]

    async def get_tarball(self, owner: str, repo: str, ref: str, *, max_bytes: int) -> bytes:
        self.calls.append(f"tarball {ref}")
        self._check(owner, repo)
        return make_tarball(self.fixture["files"], prefix=f"{owner}-{repo}-abc1234")

    def _check(self, owner: str, repo: str) -> None:
        if (owner, repo) != (self.fixture["owner"], self.fixture["repo"]):
            raise InvalidInput(f"GitHub resource not found (or private): {owner}/{repo}")


def make_tarball(files: dict[str, str], prefix: str = "acme-tiny-api-abc1234") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{prefix}/{path}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeFetcher:
    """`HttpFetcher` serving canned responses; a list value is consumed one item per call."""

    def __init__(self, routes: Mapping[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = dict(routes or {})
        self.calls: list[str] = []

    async def get(self, url: str, *, headers: Mapping[str, str] | None = None, max_bytes: int | None = None) -> FetchResponse:
        self.calls.append(url)
        route = self.routes.get(url)
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(route, Exception):
            raise route
        if route is None:
            return FetchResponse(url, 404, b"not found", {"content-type": "text/plain"})
        if isinstance(route, FetchResponse):
            return route
        return FetchResponse(url, 200, route.encode(), {"content-type": "text/html; charset=utf-8"})


def make_ctx(
    *,
    message: str = "hello",
    intent: Intent | None = None,
    limits: ExecutionLimits | None = None,
    export_mode: str = "explicit",
    source_policy: SourcePolicy | None = None,
) -> RunContext:
    return RunContext(
        conversation_id=uuid4(),
        turn_id=uuid4(),
        message=message,
        intent=intent or Intent(),
        limits=limits or ExecutionLimits(retry_backoff_s=0),
        export_policy=ExportPolicy(export_mode),  # type: ignore[arg-type]
        source_policy=source_policy or SourcePolicy(),
    )


def drain_events(ctx: RunContext) -> list[Any]:
    events = []
    while not ctx.events.empty():
        event = ctx.events.get_nowait()
        if event is not None:
            events.append(event)
    return events
