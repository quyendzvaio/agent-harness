"""Build a full container with in-memory infrastructure and swappable parts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from agent_harness.adapters.memory.artifact_store import MemoryArtifactStore
from agent_harness.adapters.memory.repositories import MemoryDatabase, MemorySourceCache, MemoryUnitOfWork
from agent_harness.api.app import create_app
from agent_harness.bootstrap.container import Container, Overrides, build_container
from agent_harness.config.settings import Settings
from agent_harness.domain.policies import ExecutionLimits
from agent_harness.harness.scripted import ScriptedEngine
from fakes import BLOG_URL, FakeFetcher, FakeGithubClient, blog_html


def make_settings(**overrides: Any) -> Settings:
    overrides.setdefault("limits", ExecutionLimits(retry_backoff_s=0))
    return Settings(_env_file=None, llm_api_key=None, **overrides)


async def make_container(
    settings: Settings | None = None,
    *,
    script: Any = None,
    chat_model: Any = None,
    fallback_models: Sequence[Any] = (),
    github: Any = None,
    fetcher: Any = None,
    artifact_store: Any = None,
    db: MemoryDatabase | None = None,
    **extra: Any,
) -> Container:
    db = db or MemoryDatabase()
    overrides = Overrides(
        uow_factory=extra.pop("uow_factory", None) or (lambda: MemoryUnitOfWork(db)),
        source_cache=extra.pop("source_cache", None) or MemorySourceCache(),
        artifact_store=artifact_store or MemoryArtifactStore(),
        http_fetcher=fetcher or FakeFetcher({BLOG_URL: blog_html()}),
        github_client=github or FakeGithubClient(),
        engine_factory=extra.pop("engine_factory", None)
        or ((lambda executor: ScriptedEngine(executor, script)) if script is not None else None),
        chat_model=chat_model,
        fallback_models=list(fallback_models),
        code_graph_builder=extra.pop("code_graph_builder", False),  # opt in per test
        **extra,
    )
    container = await build_container(settings or make_settings(), overrides)
    container.db = db  # type: ignore[attr-defined]  # handy for assertions
    return container


def client(container: Container) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(container)), base_url="http://test")


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines.get("data", "{}"))))
    return events


async def chat(http: httpx.AsyncClient, message: str, conversation_id: str | None = None, **headers: str):
    body: dict[str, Any] = {"message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    response = await http.post("/v1/chat", json=body, headers=headers)
    return response, parse_sse(response.text) if response.status_code == 200 else []


def kinds(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _ in events]


def data_of(events: list[tuple[str, dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    return [data for name, data in events if name == kind]
