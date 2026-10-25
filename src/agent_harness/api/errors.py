from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent_harness.domain.errors import HarnessError

STATUS_BY_CODE = {
    "not_found": 404,
    "unauthorized": 401,
    "invalid_input": 422,
    "policy_denied": 403,
    "capability_unavailable": 503,
    "limit_exceeded": 429,
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HarnessError)
    async def harness_error(request: Request, exc: HarnessError) -> JSONResponse:
        status = STATUS_BY_CODE.get(exc.code, 500)
        headers = {"WWW-Authenticate": "ApiKey"} if status == 401 else None
        return JSONResponse({"error": exc.to_dict()}, status_code=status, headers=headers)
