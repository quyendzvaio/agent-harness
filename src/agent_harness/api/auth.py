"""Optional API-key auth (PLAN §4.6). Swapping to JWT/OAuth only touches this file."""

from __future__ import annotations

import secrets

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from agent_harness.config.settings import Settings
from agent_harness.domain.errors import Unauthorized

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _settings(request: Request) -> Settings:
    return request.app.state.container.settings


def _matches(given: str | None, *expected: object) -> bool:
    return given is not None and any(
        e is not None and secrets.compare_digest(given.encode(), e.get_secret_value().encode())  # type: ignore[attr-defined]
        for e in expected
    )


def require_user(request: Request, api_key: str | None = Depends(_header)) -> None:
    settings = _settings(request)
    if settings.auth_enabled and not _matches(api_key, settings.api_key, settings.admin_api_key):
        raise Unauthorized("missing or invalid X-API-Key")


def require_admin(request: Request, api_key: str | None = Depends(_header)) -> None:
    settings = _settings(request)
    if settings.auth_enabled and not _matches(api_key, settings.admin_api_key):
        raise Unauthorized("admin API key required")
