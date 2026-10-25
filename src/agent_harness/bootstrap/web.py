from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_harness.api.app import create_app
from agent_harness.bootstrap.container import Overrides, build_container
from agent_harness.config.settings import Settings


class _LazyContainer:
    """Placeholder until the lifespan has built the real container."""

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("application is not started")


def build_app(settings: Settings | None = None, overrides: Overrides | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await build_container(settings, overrides)
        app.state.container = container
        try:
            yield
        finally:
            await container.aclose()

    return create_app(_LazyContainer(), lifespan=lifespan)
