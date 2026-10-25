"""Domain errors. Each error carries a stable `code` that API and SSE expose."""

from __future__ import annotations


class HarnessError(Exception):
    code = "internal"
    #: Fatal errors abort the whole turn; non-fatal ones are reported back to the model.
    fatal = False

    def __init__(self, message: str = "", **details: object) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, **({"details": self.details} if self.details else {})}


class PolicyDenied(HarnessError):
    code = "policy_denied"


class LimitExceeded(HarnessError):
    code = "limit_exceeded"
    fatal = True


class CapabilityUnavailable(HarnessError):
    code = "capability_unavailable"


class InvalidInput(HarnessError):
    code = "invalid_input"


class ToolFailed(HarnessError):
    code = "tool_failed"


class TransientError(HarnessError):
    """Temporary failure (network timeout, 5xx, 429) that is safe to retry."""

    code = "tool_failed"


class ModelError(HarnessError):
    code = "model_error"
    fatal = True


class NotFound(HarnessError):
    code = "not_found"


class Unauthorized(HarnessError):
    code = "unauthorized"
