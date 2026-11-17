"""Typed error codes and the single mapping to HTTP responses and SSE frames.

Client-facing messages are short, generic and in English; the UI shows a translated
text per code. Internal detail goes to logs only and is never sent to the client.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    RATE_LIMIT = "RATE_LIMIT"
    PROVIDER_DOWN = "PROVIDER_DOWN"
    TIMEOUT = "TIMEOUT"
    KB_UNAVAILABLE = "KB_UNAVAILABLE"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    INTERNAL = "INTERNAL"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.RATE_LIMIT: 429,
    ErrorCode.PROVIDER_DOWN: 502,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.KB_UNAVAILABLE: 503,
    ErrorCode.SESSION_NOT_FOUND: 404,
    ErrorCode.NOT_CONFIGURED: 503,
    ErrorCode.INTERNAL: 500,
}

PUBLIC_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.INVALID_INPUT: "The request was not valid.",
    ErrorCode.RATE_LIMIT: "Too many messages. Please wait and try again.",
    ErrorCode.PROVIDER_DOWN: "The answer service is unavailable right now.",
    ErrorCode.TIMEOUT: "The answer took too long. Please try again.",
    ErrorCode.KB_UNAVAILABLE: "The information library is unavailable right now.",
    ErrorCode.SESSION_NOT_FOUND: "This conversation was not found.",
    ErrorCode.NOT_CONFIGURED: "The assistant is not configured yet.",
    ErrorCode.INTERNAL: "Something went wrong. Please try again.",
}


@dataclass(eq=False)
class ChatError(Exception):
    """An error with a public code. ``detail`` is for logs only."""

    code: ErrorCode
    detail: str = ""
    retry_after: int | None = None

    def __post_init__(self) -> None:
        super().__init__(f"{self.code}: {self.detail}" if self.detail else str(self.code))

    @property
    def status(self) -> int:
        return HTTP_STATUS[self.code]

    @property
    def public_message(self) -> str:
        return PUBLIC_MESSAGE[self.code]


def classify(exc: BaseException) -> ChatError:
    """Turn any exception into a ChatError without leaking its text to clients."""
    if isinstance(exc, ChatError):
        return exc
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return ChatError(ErrorCode.TIMEOUT, detail=type(exc).__name__)
    return ChatError(ErrorCode.INTERNAL, detail=type(exc).__name__)


def to_http(error: ChatError) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Return (status, JSON body, extra headers) for a non-streaming response."""
    headers: dict[str, str] = {}
    if error.retry_after is not None:
        headers["Retry-After"] = str(error.retry_after)
    body: dict[str, Any] = {"error": {"code": error.code.value, "message": error.public_message}}
    return error.status, body, headers


def to_sse_frame(error: ChatError) -> dict[str, Any]:
    """Return the typed SSE ``error`` frame payload."""
    frame: dict[str, Any] = {
        "type": "error",
        "code": error.code.value,
        "message": error.public_message,
    }
    if error.retry_after is not None:
        frame["retryAfter"] = error.retry_after
    return frame
