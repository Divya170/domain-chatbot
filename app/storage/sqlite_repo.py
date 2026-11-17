"""SQLite chat storage (standard-library ``sqlite3``).

* One connection per call, run in a worker thread with ``asyncio.to_thread``.
* WAL journal, ``foreign_keys=ON``, busy timeout for concurrent writers.
* Versioned migrations through ``PRAGMA user_version``. Never edit an old migration;
  append a new one.
* Every query is parameterised.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from app.storage.base import (
    MessageRecord,
    Rating,
    SessionRecord,
    Source,
    from_iso,
    to_iso,
    utc_now,
)

T = TypeVar("T")

MIGRATIONS: tuple[str, ...] = (
    # 1: initial schema (sessions.client_id is the D1 ownership column)
    """
    CREATE TABLE sessions (
      id TEXT PRIMARY KEY,
      client_id TEXT NOT NULL,
      created_at TEXT NOT NULL,
      last_message_at TEXT NOT NULL,
      message_count INTEGER NOT NULL DEFAULT 0,
      language TEXT,
      expires_at TEXT NOT NULL
    );
    CREATE TABLE messages (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      role TEXT NOT NULL CHECK (role IN ('user','assistant')),
      content TEXT NOT NULL,
      language TEXT,
      sources_json TEXT,
      input_tokens INTEGER,
      output_tokens INTEGER,
      latency_ms INTEGER,
      prompt_version TEXT,
      feedback TEXT CHECK (feedback IN ('up','down')),
      feedback_note TEXT,
      error_code TEXT,
      superseded INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE INDEX idx_messages_session ON messages(session_id, created_at);
    CREATE INDEX idx_sessions_expires ON sessions(expires_at);
    CREATE INDEX idx_sessions_client ON sessions(client_id);
    """,
)

_MESSAGE_COLUMNS = (
    "id, session_id, role, content, language, sources_json, input_tokens, output_tokens, "
    "latency_ms, prompt_version, feedback, feedback_note, error_code, superseded, created_at"
)


def new_id() -> str:
    return secrets.token_urlsafe(18)


def _session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        client_id=row["client_id"],
        created_at=from_iso(row["created_at"]),
        last_message_at=from_iso(row["last_message_at"]),
        message_count=row["message_count"],
        language=row["language"],
        expires_at=from_iso(row["expires_at"]),
    )


def _sources_to_json(sources: Sequence[Source]) -> str | None:
    if not sources:
        return None
    return json.dumps(
        [{"file": s.file, "title": s.title, "heading": s.heading} for s in sources],
        ensure_ascii=False,
    )


def _sources_from_json(value: str | None) -> tuple[Source, ...]:
    if not value:
        return ()
    data: list[dict[str, Any]] = json.loads(value)
    return tuple(Source(str(d["file"]), str(d["title"]), str(d["heading"])) for d in data)


def _message(row: sqlite3.Row) -> MessageRecord:
    return MessageRecord(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        created_at=from_iso(row["created_at"]),
        language=row["language"],
        sources=_sources_from_json(row["sources_json"]),
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        latency_ms=row["latency_ms"],
        prompt_version=row["prompt_version"],
        feedback=row["feedback"],
        feedback_note=row["feedback_note"],
        error_code=row["error_code"],
        superseded=bool(row["superseded"]),
    )


class SqliteRepository:
    def __init__(
        self,
        path: Path,
        *,
        ttl_days: int,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = path
        self.ttl = timedelta(days=ttl_days)
        self._clock = clock

    # -- plumbing ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    async def _run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def work() -> T:
            with closing(self._connect()) as conn:
                return fn(conn)

        return await asyncio.to_thread(work)

    def migrate_sync(self) -> int:
        """Create the directory, enable WAL and apply pending migrations."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in (s.strip() for s in script.split(";")):
                        if statement:
                            conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {int(number)}")
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    async def migrate(self) -> int:
        return await asyncio.to_thread(self.migrate_sync)

    async def ping(self) -> bool:
        try:
            return await self._run(lambda c: c.execute("SELECT 1").fetchone()[0] == 1)
        except sqlite3.Error:
            return False

    # -- sessions ------------------------------------------------------------------
    async def create_session(self, client_id: str, language: str | None) -> SessionRecord:
        now = self._clock()
        record = SessionRecord(new_id(), client_id, now, now, 0, language, now + self.ttl)

        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO sessions (id, client_id, created_at, last_message_at, "
                "message_count, language, expires_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (
                    record.id,
                    client_id,
                    to_iso(now),
                    to_iso(now),
                    language,
                    to_iso(record.expires_at),
                ),
            )

        await self._run(work)
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        def work(conn: sqlite3.Connection) -> SessionRecord | None:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return _session(row) if row else None

        return await self._run(work)

    async def delete_session(self, session_id: str) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            return conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0

        return await self._run(work)

    async def delete_expired(self, now: datetime) -> int:
        def work(conn: sqlite3.Connection) -> int:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (to_iso(now),))
            return cursor.rowcount

        return await self._run(work)

    # -- messages ------------------------------------------------------------------
    async def add_messages(
        self,
        session_id: str,
        messages: Sequence[MessageRecord],
        *,
        supersede_ids: Sequence[str] = (),
    ) -> None:
        now = self._clock()

        def work(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for message_id in supersede_ids:
                    conn.execute(
                        "UPDATE messages SET superseded = 1 WHERE id = ? AND session_id = ?",
                        (message_id, session_id),
                    )
                for m in messages:
                    conn.execute(
                        f"INSERT INTO messages ({_MESSAGE_COLUMNS}) "  # noqa: S608 - constant
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            m.id,
                            session_id,
                            m.role,
                            m.content,
                            m.language,
                            _sources_to_json(m.sources),
                            m.input_tokens,
                            m.output_tokens,
                            m.latency_ms,
                            m.prompt_version,
                            m.feedback,
                            m.feedback_note,
                            m.error_code,
                            int(m.superseded),
                            to_iso(m.created_at),
                        ),
                    )
                language = next((m.language for m in reversed(messages) if m.language), None)
                conn.execute(
                    "UPDATE sessions SET last_message_at = ?, expires_at = ?, "
                    "message_count = message_count + ?, language = COALESCE(?, language) "
                    "WHERE id = ?",
                    (to_iso(now), to_iso(now + self.ttl), len(messages), language, session_id),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        await self._run(work)

    async def list_messages(self, session_id: str) -> list[MessageRecord]:
        def work(conn: sqlite3.Connection) -> list[MessageRecord]:
            rows = conn.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE session_id = ? "  # noqa: S608
                "ORDER BY created_at, rowid",
                (session_id,),
            ).fetchall()
            return [_message(r) for r in rows]

        return await self._run(work)

    async def get_message(self, message_id: str) -> MessageRecord | None:
        def work(conn: sqlite3.Connection) -> MessageRecord | None:
            row = conn.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE id = ?",  # noqa: S608
                (message_id,),
            ).fetchone()
            return _message(row) if row else None

        return await self._run(work)

    async def set_feedback(self, message_id: str, rating: Rating, note: str | None) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "UPDATE messages SET feedback = ?, feedback_note = ? "
                "WHERE id = ? AND role = 'assistant'",
                (rating, note, message_id),
            )
            return cursor.rowcount > 0

        return await self._run(work)

    async def count_assistant_messages_since(self, session_id: str, since: datetime) -> int:
        def work(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'assistant' "
                "AND created_at >= ?",
                (session_id, to_iso(since)),
            ).fetchone()
            return int(row[0])

        return await self._run(work)
