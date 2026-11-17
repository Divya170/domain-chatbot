"""Storage records and repository protocols used by the chat service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

Role = Literal["user", "assistant"]
Rating = Literal["up", "down"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    """Fixed-width ISO-8601 UTC string, so text comparison equals time comparison."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    client_id: str
    created_at: datetime
    last_message_at: datetime
    message_count: int
    language: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Source:
    file: str
    title: str
    heading: str


@dataclass(frozen=True, slots=True)
class MessageRecord:
    id: str
    session_id: str
    role: Role
    content: str
    created_at: datetime
    language: str | None = None
    sources: tuple[Source, ...] = field(default=())
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    prompt_version: str | None = None
    feedback: Rating | None = None
    feedback_note: str | None = None
    error_code: str | None = None
    superseded: bool = False


class SessionRepository(Protocol):
    async def create_session(self, client_id: str, language: str | None) -> SessionRecord: ...

    async def get_session(self, session_id: str) -> SessionRecord | None: ...

    async def delete_session(self, session_id: str) -> bool: ...

    async def delete_expired(self, now: datetime) -> int: ...

    async def ping(self) -> bool: ...


class MessageRepository(Protocol):
    async def add_messages(
        self,
        session_id: str,
        messages: Sequence[MessageRecord],
        *,
        supersede_ids: Sequence[str] = (),
    ) -> None: ...

    async def list_messages(self, session_id: str) -> list[MessageRecord]: ...

    async def get_message(self, message_id: str) -> MessageRecord | None: ...

    async def set_feedback(self, message_id: str, rating: Rating, note: str | None) -> bool: ...

    async def count_assistant_messages_since(self, session_id: str, since: datetime) -> int: ...


class ChatRepository(SessionRepository, MessageRepository, Protocol):
    """Both protocols together; the SQLite and memory backends implement this."""
