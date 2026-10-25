from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from agent_harness.api.errors import install_error_handlers
from agent_harness.api.routers import artifacts, chat, conversations, health, skills


def create_app(container: Any, *, lifespan: Any = None) -> FastAPI:
    """`container` exposes `settings`, the application services and `health_checks` (see bootstrap)."""
    app = FastAPI(title="Research Agent Harness", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    install_error_handlers(app)
    for module in (health, chat, conversations, artifacts, skills):
        app.include_router(module.router)
    return app
