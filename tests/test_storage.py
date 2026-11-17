from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.storage.base import MessageRecord, Source, from_iso, to_iso
from app.storage.memory_repo import MemoryRepository
from app.storage.sqlite_repo import SqliteRepository, new_id

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture(params=["sqlite", "memory"])
async def repo(
    request: pytest.FixtureRequest, tmp_path: Path, clock: Clock
) -> AsyncIterator[SqliteRepository | MemoryRepository]:
    if request.param == "sqlite":
        r: SqliteRepository | MemoryRepository = SqliteRepository(
            tmp_path / "sub" / "chat.db", ttl_days=30, clock=clock
        )
    else:
        r = MemoryRepository(ttl_days=30, clock=clock)
    await r.migrate()
    yield r


def msg(session_id: str, role: str, content: str, when: datetime, **kw: object) -> MessageRecord:
    return MessageRecord(
        id=new_id(),
        session_id=session_id,
        role=role,
        content=content,  # type: ignore[arg-type]
        created_at=when,
        **kw,
    )  # type: ignore[arg-type]


async def test_session_lifecycle(repo: SqliteRepository | MemoryRepository, clock: Clock) -> None:
    session = await repo.create_session("client-1", "ta")
    assert len(session.id) >= 20
    loaded = await repo.get_session(session.id)
    assert loaded is not None
    assert loaded.client_id == "client-1" and loaded.language == "ta"
    assert loaded.expires_at == clock.now + timedelta(days=30)
    assert await repo.get_session("nope") is None
    assert await repo.ping()


async def test_messages_roundtrip_and_session_update(
    repo: SqliteRepository | MemoryRepository, clock: Clock
) -> None:
    s = await repo.create_session("c", "en")
    clock.advance(minutes=1)
    user = msg(s.id, "user", "தமிழ் question", clock.now, language="ta")
    answer = msg(
        s.id,
        "assistant",
        "answer",
        clock.now,
        language="ta",
        sources=(Source("schemes/a.md", "A", "Overview"),),
        input_tokens=10,
        output_tokens=5,
        latency_ms=99,
        prompt_version="system.v1",
    )
    await repo.add_messages(s.id, [user, answer])
    stored = await repo.list_messages(s.id)
    assert [m.id for m in stored] == [user.id, answer.id]
    assert stored[1].sources == (Source("schemes/a.md", "A", "Overview"),)
    assert stored[1].input_tokens == 10 and stored[1].prompt_version == "system.v1"
    assert stored[0].content == "தமிழ் question"
    session = await repo.get_session(s.id)
    assert session is not None
    assert session.message_count == 2 and session.language == "ta"
    assert session.last_message_at == clock.now
    assert session.expires_at == clock.now + timedelta(days=30)
    assert await repo.get_message(answer.id) == stored[1]


async def test_injection_string_is_stored_verbatim(
    repo: SqliteRepository | MemoryRepository, clock: Clock
) -> None:
    s = await repo.create_session("c", None)
    evil = "x'); DROP TABLE messages; --"
    await repo.add_messages(s.id, [msg(s.id, "user", evil, clock.now)])
    assert (await repo.list_messages(s.id))[0].content == evil
    assert await repo.get_session(evil) is None


async def test_supersede_and_feedback(
    repo: SqliteRepository | MemoryRepository, clock: Clock
) -> None:
    s = await repo.create_session("c", None)
    user = msg(s.id, "user", "q", clock.now)
    old = msg(s.id, "assistant", "old", clock.now)
    await repo.add_messages(s.id, [user, old])
    clock.advance(seconds=5)
    new = msg(s.id, "assistant", "new", clock.now)
    await repo.add_messages(s.id, [new], supersede_ids=[old.id])
    stored = {m.id: m for m in await repo.list_messages(s.id)}
    assert stored[old.id].superseded and not stored[new.id].superseded
    assert await repo.set_feedback(new.id, "up", "useful")
    assert not await repo.set_feedback(user.id, "down", None)  # only assistant messages
    assert not await repo.set_feedback("missing", "up", None)
    updated = await repo.get_message(new.id)
    assert updated is not None and updated.feedback == "up" and updated.feedback_note == "useful"


async def test_cascade_delete(repo: SqliteRepository | MemoryRepository, clock: Clock) -> None:
    s = await repo.create_session("c", None)
    m = msg(s.id, "user", "q", clock.now)
    await repo.add_messages(s.id, [m])
    assert await repo.delete_session(s.id)
    assert await repo.get_message(m.id) is None
    assert await repo.list_messages(s.id) == []
    assert not await repo.delete_session(s.id)


async def test_ttl_cleanup_removes_only_expired(
    repo: SqliteRepository | MemoryRepository, clock: Clock
) -> None:
    old = await repo.create_session("c", None)
    clock.advance(days=20)
    fresh = await repo.create_session("c", None)
    clock.advance(days=11)  # old is 31 days old, fresh 11
    assert await repo.delete_expired(clock.now) == 1
    assert await repo.get_session(old.id) is None
    assert await repo.get_session(fresh.id) is not None


async def test_activity_extends_expiry(
    repo: SqliteRepository | MemoryRepository, clock: Clock
) -> None:
    s = await repo.create_session("c", None)
    clock.advance(days=25)
    await repo.add_messages(s.id, [msg(s.id, "user", "q", clock.now)])
    clock.advance(days=10)
    assert await repo.delete_expired(clock.now) == 0


async def test_daily_count(repo: SqliteRepository | MemoryRepository, clock: Clock) -> None:
    s = await repo.create_session("c", None)
    await repo.add_messages(s.id, [msg(s.id, "assistant", "a", clock.now - timedelta(days=2))])
    await repo.add_messages(
        s.id, [msg(s.id, "user", "q", clock.now), msg(s.id, "assistant", "b", clock.now)]
    )
    assert await repo.count_assistant_messages_since(s.id, clock.now - timedelta(days=1)) == 1


async def test_concurrent_writes(repo: SqliteRepository | MemoryRepository, clock: Clock) -> None:
    s = await repo.create_session("c", None)

    async def writer(prefix: str) -> None:
        for i in range(20):
            await repo.add_messages(s.id, [msg(s.id, "user", f"{prefix}{i}", clock.now)])

    await asyncio.gather(writer("a"), writer("b"))
    assert len(await repo.list_messages(s.id)) == 40
    session = await repo.get_session(s.id)
    assert session is not None and session.message_count == 40


async def test_sqlite_schema_wal_and_migrations(tmp_path: Path, clock: Clock) -> None:
    path = tmp_path / "data" / "chat.db"
    repo = SqliteRepository(path, ttl_days=30, clock=clock)
    assert await repo.migrate() == 1
    assert await repo.migrate() == 1  # idempotent
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"sessions", "messages"} <= tables
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"idx_messages_session", "idx_sessions_expires", "idx_sessions_client"} <= indexes
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at) "
                "VALUES ('m', 's', 'system', 'x', 'now')"
            )


async def test_sqlite_foreign_key_enforced(tmp_path: Path, clock: Clock) -> None:
    repo = SqliteRepository(tmp_path / "chat.db", ttl_days=30, clock=clock)
    await repo.migrate()
    with pytest.raises(sqlite3.IntegrityError):
        await repo.add_messages("no-such-session", [msg("no-such-session", "user", "q", clock.now)])


def test_iso_roundtrip_is_sortable() -> None:
    a = datetime(2026, 1, 1, 0, 0, 0, 5, tzinfo=UTC)
    b = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert from_iso(to_iso(a)) == a
    assert to_iso(a) < to_iso(b)
