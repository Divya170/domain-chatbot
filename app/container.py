"""Builds every service from settings. The chat service only sees the protocols
(``Retriever``, ``LlmProvider``, ``ChatRepository``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings
from app.domain import DOMAIN, DomainSettings
from app.knowledge.index import KnowledgeIndex
from app.knowledge.keyword import KeywordRetriever
from app.llm.base import LlmProvider
from app.llm.gemini import GeminiProvider, create_gemini_client
from app.security.cookies import CookieSigner
from app.security.rate_limit import RateLimiter
from app.services.chat_service import ChatLimits, ChatService
from app.storage.memory_repo import MemoryRepository
from app.storage.sqlite_repo import SqliteRepository

logger = logging.getLogger(__name__)

Repository = SqliteRepository | MemoryRepository


@dataclass
class Container:
    settings: Settings
    domain: DomainSettings
    index: KnowledgeIndex
    provider: LlmProvider
    gemini: GeminiProvider | None
    repo: Repository
    service: ChatService
    signer: CookieSigner
    model_verified: bool | None = None


def _gemini(settings: Settings) -> GeminiProvider:
    if settings.gemini_api_key is None or not settings.gemini_model:
        return GeminiProvider(None, settings.gemini_model)
    try:
        client = create_gemini_client(
            settings.gemini_api_key.get_secret_value(), settings.llm_timeout_seconds
        )
    except ImportError:
        logger.error("google-genai is not installed; chat is disabled")
        return GeminiProvider(None, settings.gemini_model)
    return GeminiProvider(client, settings.gemini_model)


def build_container(
    settings: Settings,
    *,
    provider: LlmProvider | None = None,
    repo: Repository | None = None,
    domain: DomainSettings = DOMAIN,
) -> Container:
    index = KnowledgeIndex(
        settings.knowledge_dir, reload_check_seconds=settings.kb_reload_check_seconds
    )
    retriever = KeywordRetriever(
        index.snapshot,
        max_context_tokens=settings.kb_max_context_tokens,
        heading_keywords=domain.heading_keywords,
    )
    gemini: GeminiProvider | None = None
    if provider is None:
        gemini = _gemini(settings)
        provider = gemini
    if repo is None:
        repo = (
            SqliteRepository(settings.sqlite_path, ttl_days=settings.session_ttl_days)
            if settings.storage_backend == "sqlite"
            else MemoryRepository(ttl_days=settings.session_ttl_days)
        )
    limiter = RateLimiter(
        per_minute=settings.rate_limit_per_minute,
        daily_cap=settings.daily_message_cap,
        daily_counter=repo.count_assistant_messages_since,
    )
    limits = ChatLimits(
        max_message_length=settings.max_message_length,
        max_history_messages=settings.max_history_messages,
        prompt_max_input_tokens=settings.prompt_max_input_tokens,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
        thinking_budget=settings.llm_thinking_budget,
    )
    service = ChatService(
        domain=domain,
        retriever=retriever,
        kb_ready=lambda: index.snapshot() is not None,
        provider=provider,
        repo=repo,
        limiter=limiter,
        limits=limits,
    )
    secret = settings.session_secret.get_secret_value() if settings.session_secret else None
    return Container(
        settings=settings,
        domain=domain,
        index=index,
        provider=provider,
        gemini=gemini,
        repo=repo,
        service=service,
        signer=CookieSigner(secret),
    )
