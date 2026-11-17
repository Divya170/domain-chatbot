"""Pydantic v2 request/response models. JSON uses camelCase (``sessionId``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.services.chat_service import ChatEvent, DoneEvent, ErrorEvent, SourcesEvent, StartEvent
from app.services.errors import to_sse_frame
from app.storage.base import MessageRecord, Source

# Hard ceiling at the schema level; the configured MAX_MESSAGE_LENGTH (≤ this) is
# enforced in the chat service and reported as INVALID_INPUT.
MESSAGE_HARD_LIMIT = 20_000
ID_MAX = 64


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class ChatIn(ApiModel):
    message: str = Field(max_length=MESSAGE_HARD_LIMIT)
    language: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=ID_MAX)
    # The old client sent ``history``; it is accepted and ignored (D10).


class RegenerateIn(ApiModel):
    session_id: str = Field(min_length=1, max_length=ID_MAX)
    message_id: str = Field(min_length=1, max_length=ID_MAX)


class FeedbackIn(ApiModel):
    rating: Literal["up", "down"]
    note: str | None = Field(default=None, max_length=1000)


class SourceOut(ApiModel):
    file: str
    title: str
    heading: str

    @classmethod
    def of(cls, source: Source) -> SourceOut:
        return cls(file=source.file, title=source.title, heading=source.heading)


class UsageOut(ApiModel):
    input: int
    output: int


class ErrorOut(ApiModel):
    code: str
    message: str
    retry_after: int | None = None


class ChatOut(ApiModel):
    reply: str
    message_id: str | None = None
    session_id: str | None = None
    sources: list[SourceOut] = Field(default_factory=list)
    usage: UsageOut | None = None
    latency_ms: int | None = None
    error: ErrorOut | None = None


class MessageOut(ApiModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    language: str | None
    sources: list[SourceOut]
    feedback: Literal["up", "down"] | None
    error_code: str | None
    created_at: str

    @classmethod
    def of(cls, m: MessageRecord) -> MessageOut:
        return cls(
            id=m.id,
            role=m.role,
            content=m.content,
            language=m.language,
            sources=[SourceOut.of(s) for s in m.sources],
            feedback=m.feedback,
            error_code=m.error_code,
            created_at=m.created_at.isoformat(),
        )


def event_payload(event: ChatEvent) -> dict[str, object]:
    """The SSE frame for an event (see docs/ARCHITECTURE.md for the contract)."""
    if isinstance(event, StartEvent):
        return {"type": "start", "messageId": event.message_id, "sessionId": event.session_id}
    if isinstance(event, SourcesEvent):
        return {
            "type": "sources",
            "sources": [SourceOut.of(s).model_dump(by_alias=True) for s in event.sources],
        }
    if isinstance(event, DoneEvent):
        usage = (
            {"input": event.usage.input_tokens, "output": event.usage.output_tokens}
            if event.usage
            else None
        )
        return {
            "type": "done",
            "messageId": event.message_id,
            "usage": usage,
            "latencyMs": event.latency_ms,
        }
    if isinstance(event, ErrorEvent):
        return to_sse_frame(event.error)
    return {"type": "chunk", "text": event.text}
