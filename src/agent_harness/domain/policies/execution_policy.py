"""Rule R3: per-turn execution budget."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExecutionLimits(BaseModel):
    max_model_calls: int = Field(default=12, ge=1)
    max_tool_calls: int = Field(default=24, ge=0)
    turn_timeout_s: float = Field(default=180.0, gt=0)
    max_fetch_bytes: int = Field(default=2_000_000, ge=1024)
    max_retries: int = Field(default=2, ge=0)
    retry_backoff_s: float = Field(default=0.5, ge=0)
    hook_timeout_s: float = Field(default=2.0, gt=0)
