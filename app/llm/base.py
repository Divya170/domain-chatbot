"""Provider-neutral LLM interface used by the chat service."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["user", "model"]


@dataclass(frozen=True, slots=True)
class Turn:
    role: Role
    text: str


@dataclass(frozen=True, slots=True)
class LlmRequest:
    system_instruction: str
    contents: tuple[Turn, ...]
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    thinking_budget: int | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class LlmChunk:
    """A piece of streamed output. ``usage`` is set on the chunk that carries it."""

    text: str = ""
    usage: Usage | None = None


class LlmProvider(Protocol):
    """Streams a reply. Failures are raised as ``ChatError`` with a typed code."""

    @property
    def name(self) -> str: ...

    @property
    def configured(self) -> bool: ...

    def stream(self, request: LlmRequest) -> AsyncGenerator[LlmChunk, None]: ...
