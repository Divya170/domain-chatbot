"""Chat orchestration: session → history → retrieve → prompt → stream → persist.

Two steps per request:

* ``prepare_*`` validates, rate-limits, resolves the session (ownership-checked),
  builds the prompt. It raises ``ChatError`` so the API can answer with a proper HTTP
  status *before* any stream starts.
* ``run`` streams events and never raises: a provider failure becomes a fallback
  message in the selected language plus a typed ``ErrorEvent`` (never-throw contract).

Stored user text is redacted (D8). Assistant text is stored as produced: the model
never sees the user's raw numbers, and legitimate helpline numbers from the knowledge
files must survive.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime

from app.domain import DomainSettings, Language
from app.knowledge.base import RetrievalQuery, Retriever
from app.llm.base import LlmProvider, LlmRequest, Usage
from app.logging_setup import log_content
from app.prompts.builder import build_prompt
from app.security.rate_limit import RateLimiter
from app.security.redaction import redact
from app.services.errors import ChatError, ErrorCode, classify
from app.services.history import recent_user_texts, select_history
from app.storage.base import ChatRepository, MessageRecord, Rating, Source, utc_now
from app.storage.sqlite_repo import new_id

logger = logging.getLogger(__name__)

CANCELLED = "CANCELLED"  # internal storage marker only (D9); never sent to clients
MAX_FEEDBACK_NOTE = 1000


@dataclass(frozen=True, slots=True)
class ChatLimits:
    max_message_length: int
    max_history_messages: int
    prompt_max_input_tokens: int
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    thinking_budget: int | None = None


@dataclass(frozen=True, slots=True)
class StartEvent:
    message_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class ChunkEvent:
    text: str


@dataclass(frozen=True, slots=True)
class SourcesEvent:
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class DoneEvent:
    message_id: str
    usage: Usage | None
    latency_ms: int


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    error: ChatError


ChatEvent = StartEvent | ChunkEvent | SourcesEvent | DoneEvent | ErrorEvent


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    session_id: str
    language: Language
    user_message: MessageRecord | None  # None when regenerating
    assistant_id: str
    request: LlmRequest
    sources: tuple[Source, ...]
    prompt_version: str
    supersede_ids: tuple[str, ...]
    started: float


class ChatService:
    def __init__(
        self,
        *,
        domain: DomainSettings,
        retriever: Retriever,
        kb_ready: Callable[[], bool],
        provider: LlmProvider,
        repo: ChatRepository,
        limiter: RateLimiter,
        limits: ChatLimits,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.domain = domain
        self.retriever = retriever
        self.kb_ready = kb_ready
        self.provider = provider
        self.repo = repo
        self.limiter = limiter
        self.limits = limits
        self._clock = clock
        self._monotonic = monotonic
        self._background: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ helpers
    def fallback_text(self, language: str) -> str:
        return (
            f"{self.domain.text(self.domain.fallback, language)}\n\n"
            f"{self.domain.text(self.domain.emergency, language)}"
        )

    def _check_ready(self) -> None:
        if not self.provider.configured:
            raise ChatError(ErrorCode.NOT_CONFIGURED, detail="llm provider not configured")
        if not self.kb_ready():
            raise ChatError(ErrorCode.KB_UNAVAILABLE, detail="knowledge index not loaded")

    async def owned_session_id(self, client_id: str, session_id: str) -> str:
        session = await self.repo.get_session(session_id)
        if session is None or session.client_id != client_id or session.expires_at <= self._clock():
            raise ChatError(ErrorCode.SESSION_NOT_FOUND)
        return session.id

    def _build(
        self,
        *,
        session_id: str,
        language: Language,
        question: str,
        history_messages: Sequence[MessageRecord],
        user_message: MessageRecord | None,
        supersede_ids: tuple[str, ...],
    ) -> PreparedTurn:
        pairs = select_history(
            history_messages,
            max_messages=self.limits.max_history_messages,
            token_budget=self.limits.prompt_max_input_tokens,
        )
        retrieval = self.retriever.retrieve(
            RetrievalQuery(question, language.code, recent_user_texts(pairs))
        )
        built = build_prompt(
            domain=self.domain,
            language=language,
            question=question,
            history=pairs,
            retrieval=retrieval,
            max_input_tokens=self.limits.prompt_max_input_tokens,
        )
        sources: list[Source] = []
        for scored in built.used_chunks:
            c = scored.chunk
            source = Source(c.file, c.title, c.heading)
            if source not in sources:
                sources.append(source)
        request = LlmRequest(
            system_instruction=built.system_instruction,
            contents=built.contents,
            temperature=self.limits.temperature,
            max_output_tokens=self.limits.max_output_tokens,
            timeout_seconds=self.limits.timeout_seconds,
            thinking_budget=self.limits.thinking_budget,
        )
        logger.info(
            "prompt built",
            extra={
                "session_id": session_id,
                "language": language.code,
                "chunks": len(built.used_chunks),
                "retrieval_mode": retrieval.mode,
                "history_pairs": built.history_pairs,
                "estimated_tokens": built.estimated_tokens,
                "question": log_content(question),
            },
        )
        return PreparedTurn(
            session_id=session_id,
            language=language,
            user_message=user_message,
            assistant_id=new_id(),
            request=request,
            sources=tuple(sources),
            prompt_version=built.prompt_version,
            supersede_ids=supersede_ids,
            started=self._monotonic(),
        )

    # ------------------------------------------------------------------ prepare
    async def prepare_message(
        self,
        *,
        client_id: str,
        ip: str,
        message: str,
        language: str | None,
        session_id: str | None,
    ) -> PreparedTurn:
        text = message.strip()
        if not text or len(text) > self.limits.max_message_length:
            raise ChatError(ErrorCode.INVALID_INPUT, detail="message empty or too long")
        lang = self.domain.language(language)
        self._check_ready()
        self.limiter.check_ip(ip)
        if session_id:
            sid = await self.owned_session_id(client_id, session_id)
        else:
            sid = (await self.repo.create_session(client_id, lang.code)).id
        await self.limiter.check_session(sid)
        history = await self.repo.list_messages(sid)
        redacted = redact(text)
        user = MessageRecord(
            id=new_id(),
            session_id=sid,
            role="user",
            content=redacted,
            created_at=self._clock(),
            language=lang.code,
        )
        prepared = self._build(
            session_id=sid,
            language=lang,
            question=redacted,
            history_messages=history,
            user_message=user,
            supersede_ids=(),
        )
        self.limiter.record(ip, sid)
        return prepared

    async def prepare_regenerate(
        self, *, client_id: str, ip: str, session_id: str, message_id: str
    ) -> PreparedTurn:
        self._check_ready()
        self.limiter.check_ip(ip)
        sid = await self.owned_session_id(client_id, session_id)
        await self.limiter.check_session(sid)
        messages = await self.repo.list_messages(sid)
        live_answers = [m for m in messages if m.role == "assistant" and not m.superseded]
        if not live_answers or live_answers[-1].id != message_id:
            raise ChatError(
                ErrorCode.INVALID_INPUT, detail="only the last answer can be regenerated"
            )
        target_index = next(i for i, m in enumerate(messages) if m.id == message_id)
        user_index = next(
            (i for i in range(target_index - 1, -1, -1) if messages[i].role == "user"), None
        )
        if user_index is None:
            raise ChatError(ErrorCode.INVALID_INPUT, detail="answer has no question")
        user = messages[user_index]
        prepared = self._build(
            session_id=sid,
            language=self.domain.language(user.language),
            question=user.content,
            history_messages=messages[:user_index],
            user_message=None,
            supersede_ids=(message_id,),
        )
        self.limiter.record(ip, sid)
        return prepared

    # ------------------------------------------------------------------ run
    async def run(self, turn: PreparedTurn) -> AsyncIterator[ChatEvent]:
        """Stream the reply. Never raises (except cancellation, which is persisted)."""
        parts: list[str] = []
        usage: Usage | None = None
        persisted = False
        try:
            yield StartEvent(turn.assistant_id, turn.session_id)
            error: ChatError | None = None
            try:
                async with aclosing(self.provider.stream(turn.request)) as stream:
                    async for chunk in stream:
                        if chunk.usage is not None:
                            usage = chunk.usage
                        if chunk.text:
                            parts.append(chunk.text)
                            yield ChunkEvent(chunk.text)
                if not "".join(parts).strip():
                    error = ChatError(ErrorCode.PROVIDER_DOWN, detail="empty reply")
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as exc:  # noqa: BLE001 - never-throw contract
                error = classify(exc)

            if error is not None:
                logger.warning(
                    "reply failed", extra={"code": error.code.value, "detail": error.detail}
                )
                streamed = "".join(parts)
                addition = ("\n\n" if streamed else "") + self.fallback_text(turn.language.code)
                yield ChunkEvent(addition)
                persisted = True
                await self._persist(turn, streamed + addition, usage, error.code.value)
                yield ErrorEvent(error)
                return

            yield SourcesEvent(turn.sources)
            persisted = True
            await self._persist(turn, "".join(parts), usage, None)
            yield DoneEvent(turn.assistant_id, usage, self._latency(turn))
        finally:
            if not persisted:
                self._persist_in_background(turn, "".join(parts), usage, CANCELLED)

    def _latency(self, turn: PreparedTurn) -> int:
        return int((self._monotonic() - turn.started) * 1000)

    async def _persist(
        self, turn: PreparedTurn, content: str, usage: Usage | None, error_code: str | None
    ) -> None:
        assistant = MessageRecord(
            id=turn.assistant_id,
            session_id=turn.session_id,
            role="assistant",
            content=content,
            created_at=self._clock(),
            language=turn.language.code,
            sources=turn.sources if error_code is None else (),
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            latency_ms=self._latency(turn),
            prompt_version=turn.prompt_version,
            error_code=error_code,
        )
        records = [turn.user_message, assistant] if turn.user_message else [assistant]
        try:
            await self.repo.add_messages(turn.session_id, records, supersede_ids=turn.supersede_ids)
        except Exception:
            logger.exception(
                "failed to persist messages",
                extra={"session_id": turn.session_id, "message_id": turn.assistant_id},
            )
            return
        logger.info(
            "turn stored",
            extra={
                "session_id": turn.session_id,
                "message_id": turn.assistant_id,
                "error_code": error_code,
                "input_tokens": assistant.input_tokens,
                "output_tokens": assistant.output_tokens,
                "latency_ms": assistant.latency_ms,
                "answer": log_content(content),
            },
        )

    def _persist_in_background(
        self, turn: PreparedTurn, content: str, usage: Usage | None, error_code: str
    ) -> None:
        try:
            task = asyncio.get_running_loop().create_task(
                self._persist(turn, content, usage, error_code)
            )
        except RuntimeError:
            logger.error(
                "no event loop to persist cancelled turn", extra={"session_id": turn.session_id}
            )
            return
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def drain(self) -> None:
        """Wait for background writes (used at shutdown and in tests)."""
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    # ------------------------------------------------------------------ sessions
    async def visible_messages(self, client_id: str, session_id: str) -> list[MessageRecord]:
        sid = await self.owned_session_id(client_id, session_id)
        return [m for m in await self.repo.list_messages(sid) if not m.superseded]

    async def delete_session(self, client_id: str, session_id: str) -> None:
        sid = await self.owned_session_id(client_id, session_id)
        await self.repo.delete_session(sid)

    async def feedback(
        self, client_id: str, message_id: str, rating: Rating, note: str | None
    ) -> None:
        message = await self.repo.get_message(message_id)
        if message is None or message.role != "assistant":
            raise ChatError(ErrorCode.SESSION_NOT_FOUND, detail="message not found")
        await self.owned_session_id(client_id, message.session_id)
        clean = redact(note.strip())[:MAX_FEEDBACK_NOTE] if note and note.strip() else None
        await self.repo.set_feedback(message_id, rating, clean)

    async def cleanup_expired(self) -> int:
        removed = await self.repo.delete_expired(self._clock())
        if removed:
            logger.info("expired sessions removed", extra={"count": removed})
        return removed
