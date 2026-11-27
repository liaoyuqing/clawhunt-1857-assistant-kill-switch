"""Single error envelope for every non-2xx answer of this service."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Raised by routes; rendered as {"code": ..., "message": ...} plus extra fields."""

    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        body.update(self.extra)
        return body


ASSISTANT_DISABLED = "ASSISTANT_DISABLED"
TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
SWITCH_FORBIDDEN = "SWITCH_FORBIDDEN"
SWITCH_INVALID = "SWITCH_INVALID"
RECRUITMENT_AI_DISABLED = "RECRUITMENT_AI_DISABLED"
UNAUTHENTICATED = "UNAUTHENTICATED"
