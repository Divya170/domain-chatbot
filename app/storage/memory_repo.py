"""In-memory chat storage for tests and ``STORAGE_BACKEND=memory`` (no persistence)."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from app.storage.base import MessageRecord, Rating, SessionRecord, utc_now
from app.storage.sqlite_repo import new_id


class MemoryRepository:
    def __init__(self, *, ttl_days: int, clock: Callable[[], datetime] = utc_now) -> None:
        self.ttl = timedelta(days=ttl_days)
        self._clock = clock
        self._sessions: dict[str, SessionRecord] = {}
        self._messages: dict[str, list[MessageRecord]] = {}
        self._lock = asyncio.Lock()

    async def migrate(self) -> int:
        return 0

    async def ping(self) -> bool:
        return True

    async def create_session(self, client_id: str, language: str | None) -> SessionRecord:
        now = self._clock()
        record = SessionRecord(new_id(), client_id, now, now, 0, language, now + self.ttl)
        async with self._lock:
            self._sessions[record.id] = record
            self._messages[record.id] = []
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def delete_session(self, session_id: str) -> bool:
        async with self._lock:
            self._messages.pop(session_id, None)
            return self._sessions.pop(session_id, None) is not None

    async def delete_expired(self, now: datetime) -> int:
        async with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s.expires_at <= now]
            for sid in expired:
                del self._sessions[sid]
                self._messages.pop(sid, None)
            return len(expired)

    async def add_messages(
        self,
        session_id: str,
        messages: Sequence[MessageRecord],
        *,
        supersede_ids: Sequence[str] = (),
    ) -> None:
        now = self._clock()
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError("session not found")
            stored = self._messages[session_id]
            for index, message in enumerate(stored):
                if message.id in supersede_ids:
                    stored[index] = dataclasses.replace(message, superseded=True)
            stored.extend(messages)
            language = next((m.language for m in reversed(messages) if m.language), None)
            self._sessions[session_id] = dataclasses.replace(
                session,
                last_message_at=now,
                expires_at=now + self.ttl,
                message_count=session.message_count + len(messages),
                language=language or session.language,
            )

    async def list_messages(self, session_id: str) -> list[MessageRecord]:
        return sorted(self._messages.get(session_id, []), key=lambda m: m.created_at)

    async def get_message(self, message_id: str) -> MessageRecord | None:
        for messages in self._messages.values():
            for message in messages:
                if message.id == message_id:
                    return message
        return None

    async def set_feedback(self, message_id: str, rating: Rating, note: str | None) -> bool:
        async with self._lock:
            for messages in self._messages.values():
                for index, message in enumerate(messages):
                    if message.id == message_id and message.role == "assistant":
                        messages[index] = dataclasses.replace(
                            message, feedback=rating, feedback_note=note
                        )
                        return True
        return False

    async def count_assistant_messages_since(self, session_id: str, since: datetime) -> int:
        return sum(
            1
            for m in self._messages.get(session_id, [])
            if m.role == "assistant" and m.created_at >= since
        )
