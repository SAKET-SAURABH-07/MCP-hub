"""Stable error codes. Every failure a client can see maps to one of these."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    RATE_LIMITED = "RATE_LIMITED"
    DUPLICATE_IN_PROGRESS = "DUPLICATE_IN_PROGRESS"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    UPSTREAM_AUTH = "UPSTREAM_AUTH"
    UPSTREAM_NOT_FOUND = "UPSTREAM_NOT_FOUND"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    INTERNAL = "INTERNAL"


class ToolError(Exception):
    """Raised anywhere in the pipeline; surfaced to the MCP client as an error result."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
