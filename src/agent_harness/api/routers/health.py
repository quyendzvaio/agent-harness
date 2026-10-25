from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    for name, check in request.app.state.container.health_checks.items():
        try:
            await check()
            checks[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - report every dependency
            checks[name] = f"error: {exc}"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ready" if ready else "not_ready", "checks": checks}, status_code=200 if ready else 503)
