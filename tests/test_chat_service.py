from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.domain import DOMAIN
from app.knowledge.index import KnowledgeIndex
from app.knowledge.keyword import KeywordRetriever
from app.llm.fake import FakeLlmProvider
from app.security.rate_limit import RateLimiter
from app.security.redaction import ID_TOKEN, PHONE_TOKEN
from app.services.chat_service import (
    CANCELLED,
    ChatEvent,
    ChatLimits,
    ChatService,
    ChunkEvent,
    DoneEvent,
    ErrorEvent,
    PreparedTurn,
    SourcesEvent,
    StartEvent,
)
from app.services.errors import ChatError, ErrorCode
from app.storage.memory_repo import MemoryRepository
from tests.conftest import KB_ROOT

pytestmark = pytest.mark.anyio

LIMITS = ChatLimits(
    max_message_length=4000,
    max_history_messages=12,
    prompt_max_input_tokens=12000,
    temperature=0.25,
    max_output_tokens=1200,
    timeout_seconds=5,
)


def make_service(
    provider: FakeLlmProvider | None = None,
    *,
    kb: Path = KB_ROOT,
    per_minute: int = 100,
    daily_cap: int = 1000,
    limits: ChatLimits = LIMITS,
) -> tuple[ChatService, MemoryRepository, FakeLlmProvider]:
    repo = MemoryRepository(ttl_days=30)
    index = KnowledgeIndex(kb, reload_check_seconds=0)
    retriever = KeywordRetriever(
        index.snapshot, max_context_tokens=400, heading_keywords=DOMAIN.heading_keywords
    )
    fake = provider or FakeLlmProvider("Here is the answer.")
    limiter = RateLimiter(
        per_minute=per_minute,
        daily_cap=daily_cap,
        daily_counter=repo.count_assistant_messages_since,
    )
    service = ChatService(
        domain=DOMAIN,
        retriever=retriever,
        kb_ready=lambda: index.snapshot() is not None,
        provider=fake,
        repo=repo,
        limiter=limiter,
        limits=limits,
    )
    return service, repo, fake


async def collect(events: AsyncIterator[ChatEvent]) -> list[ChatEvent]:
    return [e async for e in events]


async def ask(
    service: ChatService,
    text: str,
    *,
    session_id: str | None = None,
    language: str = "en",
    client: str = "client-a",
    ip: str = "1.1.1.1",
) -> tuple[PreparedTurn, list[ChatEvent]]:
    turn = await service.prepare_message(
        client_id=client, ip=ip, message=text, language=language, session_id=session_id
    )
    return turn, await collect(service.run(turn))


async def test_event_order_and_persistence() -> None:
    service, repo, _ = make_service()
    turn, events = await ask(service, "What documents are needed for PMMVY?")
    kinds = [type(e) for e in events]
    assert kinds[0] is StartEvent and kinds[-1] is DoneEvent and kinds[-2] is SourcesEvent
    assert all(k is ChunkEvent for k in kinds[1:-2])
    assert "".join(e.text for e in events if isinstance(e, ChunkEvent)) == "Here is the answer."
    start, done = events[0], events[-1]
    assert isinstance(start, StartEvent) and isinstance(done, DoneEvent)
    assert start.message_id == done.message_id == turn.assistant_id
    assert done.usage is not None and done.usage.output_tokens > 0
    sources = events[-2]
    assert isinstance(sources, SourcesEvent)
    assert {s.file for s in sources.sources} == {"schemes/pmmvy.md"}
    stored = await repo.list_messages(start.session_id)
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[1].id == start.message_id and stored[1].prompt_version == "system.v1"
    assert stored[1].output_tokens == done.usage.output_tokens
    assert stored[1].sources == sources.sources
    session = await repo.get_session(start.session_id)
    assert session is not None and session.client_id == "client-a"


async def test_language_reaches_prompt_and_unknown_language_defaults() -> None:
    service, _, fake = make_service()
    await ask(service, "மாத்ரு வந்தனா ஆவணங்கள்", language="ta")
    assert "Tamil" in fake.requests[-1].system_instruction
    await ask(service, "hello", language="klingon")
    assert (
        "selected language for this conversation is: English"
        in fake.requests[-1].system_instruction
    )


async def test_history_comes_from_storage() -> None:
    service, _, fake = make_service()
    turn, _ = await ask(service, "Tell me about PMMVY")
    await ask(service, "and the documents?", session_id=turn.session_id)
    contents = fake.requests[-1].contents
    assert [t.role for t in contents] == ["user", "model", "user"]
    assert contents[0].text == "Tell me about PMMVY"
    assert contents[1].text == "Here is the answer."
    assert "pmmvy" in contents[-1].text.lower()  # follow-up retrieval used recent turn


async def test_sensitive_numbers_redacted_before_model_and_storage() -> None:
    service, repo, fake = make_service()
    turn, _ = await ask(service, "My aadhaar 1234 5678 9012 and phone 9876543210")
    sent = fake.requests[-1].contents[-1].text
    assert "1234 5678 9012" not in sent and "9876543210" not in sent
    assert ID_TOKEN in sent and PHONE_TOKEN in sent
    stored = await repo.list_messages(turn.session_id)
    assert "9876543210" not in stored[0].content


@pytest.mark.parametrize("message", ["", "   ", "x" * 4001])
async def test_invalid_input(message: str) -> None:
    service, _, _ = make_service()
    with pytest.raises(ChatError) as info:
        await ask(service, message)
    assert info.value.code is ErrorCode.INVALID_INPUT


async def test_provider_failure_streams_fallback_in_language() -> None:
    failing = FakeLlmProvider(
        "partial answer here", fail_with=ChatError(ErrorCode.PROVIDER_DOWN), fail_after_chunks=1
    )
    service, repo, _ = make_service(failing)
    turn, events = await ask(service, "PMMVY?", language="ta")
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].error.code is ErrorCode.PROVIDER_DOWN
    text = "".join(e.text for e in events if isinstance(e, ChunkEvent))
    assert text.startswith("partial")
    assert DOMAIN.fallback["ta"] in text and DOMAIN.emergency["ta"] in text
    assert not any(isinstance(e, DoneEvent | SourcesEvent) for e in events)
    stored = await repo.list_messages(turn.session_id)
    assert stored[1].error_code == "PROVIDER_DOWN"


async def test_unexpected_exception_becomes_internal_without_leak() -> None:
    service, _, _ = make_service(FakeLlmProvider("x", fail_with=RuntimeError("secret key abc")))
    _, events = await ask(service, "hello")
    error = events[-1]
    assert isinstance(error, ErrorEvent) and error.error.code is ErrorCode.INTERNAL
    assert "secret" not in error.error.public_message


async def test_provider_429_maps_to_rate_limit() -> None:
    service, _, _ = make_service(
        FakeLlmProvider("x", fail_with=ChatError(ErrorCode.RATE_LIMIT, retry_after=30))
    )
    _, events = await ask(service, "hello")
    assert isinstance(events[-1], ErrorEvent) and events[-1].error.code is ErrorCode.RATE_LIMIT


async def test_empty_reply_is_an_error() -> None:
    service, _, _ = make_service(FakeLlmProvider(""))
    _, events = await ask(service, "hello")
    assert isinstance(events[-1], ErrorEvent)


async def test_failed_turn_excluded_from_next_history() -> None:
    fake = FakeLlmProvider("x", fail_with=ChatError(ErrorCode.TIMEOUT))
    service, _, _ = make_service(fake)
    turn, _ = await ask(service, "first")
    fake.fail_with = None
    fake.reply = "ok"
    await ask(service, "second", session_id=turn.session_id)
    assert len(fake.requests[-1].contents) == 1


async def test_client_disconnect_aborts_provider_and_persists_cancelled() -> None:
    fake = FakeLlmProvider("one two three four five", delay_seconds=0.01)
    service, repo, _ = make_service(fake)
    turn = await service.prepare_message(
        client_id="c", ip="ip", message="hello", language="en", session_id=None
    )
    stream = service.run(turn)
    seen = 0
    async for event in stream:
        if isinstance(event, ChunkEvent):
            seen += 1
            if seen == 2:
                break
    await stream.aclose()  # type: ignore[attr-defined]
    await service.drain()
    assert fake.closed_early
    stored = await repo.list_messages(turn.session_id)
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[1].error_code == CANCELLED
    assert stored[1].content == "one two"


async def test_task_cancellation_is_persisted() -> None:
    fake = FakeLlmProvider("a b c d e f", delay_seconds=0.05)
    service, repo, _ = make_service(fake)
    turn = await service.prepare_message(
        client_id="c", ip="ip", message="hello", language="en", session_id=None
    )

    async def consume() -> None:
        async for _ in service.run(turn):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.08)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await service.drain()
    stored = await repo.list_messages(turn.session_id)
    assert stored[-1].error_code == CANCELLED


class TestOwnership:
    async def test_other_client_cannot_use_session(self) -> None:
        service, _, _ = make_service()
        turn, _ = await ask(service, "hello", client="owner")
        for call in (
            service.visible_messages("intruder", turn.session_id),
            service.delete_session("intruder", turn.session_id),
            service.prepare_message(
                client_id="intruder",
                ip="i",
                message="hi",
                language="en",
                session_id=turn.session_id,
            ),
            service.prepare_regenerate(
                client_id="intruder",
                ip="i",
                session_id=turn.session_id,
                message_id=turn.assistant_id,
            ),
            service.feedback("intruder", turn.assistant_id, "up", None),
        ):
            with pytest.raises(ChatError) as info:
                await call
            assert info.value.code is ErrorCode.SESSION_NOT_FOUND

    async def test_unknown_session_and_delete(self) -> None:
        service, repo, _ = make_service()
        with pytest.raises(ChatError):
            await service.visible_messages("c", "missing")
        turn, _ = await ask(service, "hello", client="c")
        assert len(await service.visible_messages("c", turn.session_id)) == 2
        await service.delete_session("c", turn.session_id)
        assert await repo.get_session(turn.session_id) is None


class TestRegenerate:
    async def test_regenerate_last_answer(self) -> None:
        fake = FakeLlmProvider("first answer")
        service, repo, _ = make_service(fake)
        turn, _ = await ask(service, "What about JSY?", language="ta")
        fake.reply = "second answer"
        regen = await service.prepare_regenerate(
            client_id="client-a", ip="ip", session_id=turn.session_id, message_id=turn.assistant_id
        )
        events = await collect(service.run(regen))
        assert isinstance(events[-1], DoneEvent)
        assert regen.language.code == "ta"
        assert fake.requests[-1].contents[-1].text.endswith("What about JSY?")
        assert len(fake.requests[-1].contents) == 1  # no history before that question
        visible = await service.visible_messages("client-a", turn.session_id)
        assert [m.content for m in visible] == ["What about JSY?", "second answer"]
        all_rows = await repo.list_messages(turn.session_id)
        assert [m.superseded for m in all_rows] == [False, True, False]

    async def test_only_last_answer_can_be_regenerated(self) -> None:
        service, _, _ = make_service()
        first, _ = await ask(service, "one")
        await ask(service, "two", session_id=first.session_id)
        with pytest.raises(ChatError) as info:
            await service.prepare_regenerate(
                client_id="client-a",
                ip="ip",
                session_id=first.session_id,
                message_id=first.assistant_id,
            )
        assert info.value.code is ErrorCode.INVALID_INPUT

    async def test_regenerate_counts_against_rate_limit(self) -> None:
        service, _, _ = make_service(per_minute=1)
        turn, _ = await ask(service, "one")
        with pytest.raises(ChatError) as info:
            await service.prepare_regenerate(
                client_id="client-a",
                ip="other-ip",
                session_id=turn.session_id,
                message_id=turn.assistant_id,
            )
        assert info.value.code is ErrorCode.RATE_LIMIT

    async def test_regenerate_provider_failure(self) -> None:
        fake = FakeLlmProvider("good")
        service, _, _ = make_service(fake)
        turn, _ = await ask(service, "one")
        fake.fail_with = ChatError(ErrorCode.PROVIDER_DOWN)
        regen = await service.prepare_regenerate(
            client_id="client-a", ip="ip", session_id=turn.session_id, message_id=turn.assistant_id
        )
        events = await collect(service.run(regen))
        assert isinstance(events[-1], ErrorEvent)


class TestLimitsAndReadiness:
    async def test_rate_limit_per_ip(self) -> None:
        service, _, _ = make_service(per_minute=2)
        await ask(service, "a", client="x")
        await ask(service, "b", client="y")
        with pytest.raises(ChatError) as info:
            await ask(service, "c", client="z")
        assert info.value.code is ErrorCode.RATE_LIMIT and info.value.retry_after

    async def test_daily_cap(self) -> None:
        service, _, _ = make_service(daily_cap=1)
        turn, _ = await ask(service, "a")
        with pytest.raises(ChatError) as info:
            await ask(service, "b", session_id=turn.session_id)
        assert info.value.detail == "daily cap"

    async def test_not_configured(self) -> None:
        service, _, _ = make_service(FakeLlmProvider(configured=False))
        with pytest.raises(ChatError) as info:
            await ask(service, "hello")
        assert info.value.code is ErrorCode.NOT_CONFIGURED

    async def test_kb_unavailable(self, tmp_path: Path) -> None:
        service, _, _ = make_service(kb=tmp_path / "missing")
        with pytest.raises(ChatError) as info:
            await ask(service, "hello")
        assert info.value.code is ErrorCode.KB_UNAVAILABLE


async def test_feedback_and_cleanup() -> None:
    service, repo, _ = make_service()
    turn, _ = await ask(service, "hello")
    await service.feedback("client-a", turn.assistant_id, "down", " wrong, call 9876543210 ")
    stored = await repo.get_message(turn.assistant_id)
    assert stored is not None and stored.feedback == "down"
    assert stored.feedback_note == f"wrong, call {PHONE_TOKEN}"
    user_id = (await repo.list_messages(turn.session_id))[0].id
    with pytest.raises(ChatError):
        await service.feedback("client-a", user_id, "up", None)
    assert await service.cleanup_expired() == 0
