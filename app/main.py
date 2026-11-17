"""FastAPI application factory.

Run:  uvicorn app.main:create_app --factory --port 8000
Prod: gunicorn "app.main:create_app()" -k uvicorn.workers.UvicornWorker -w 1
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import chat, feedback, meta, sessions
from app.api.middleware import (
    BodyLimitMiddleware,
    ClientCookieMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.config import Settings, get_settings
from app.container import Container, Repository, build_container
from app.llm.base import LlmProvider
from app.logging_setup import configure_logging
from app.services.errors import ChatError, ErrorCode, to_http

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 256 * 1024
CLEANUP_INTERVAL_SECONDS = 3600


async def _cleanup_loop(container: Container) -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            await container.service.cleanup_expired()
        except Exception:
            logger.exception("session cleanup failed")


async def _verify_model(container: Container) -> None:
    if container.gemini is not None:
        container.model_verified = await container.gemini.verify_model()


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(ChatError)
    async def chat_error(_: Request, exc: ChatError) -> JSONResponse:
        if exc.code is ErrorCode.INTERNAL:
            logger.error("internal error", extra={"detail": exc.detail})
        status, body, headers = to_http(exc)
        return JSONResponse(body, status_code=status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(p) for p in err.get("loc", ())) for err in exc.errors()]
        status, body, headers = to_http(ChatError(ErrorCode.INVALID_INPUT))
        body["error"]["fields"] = fields
        return JSONResponse(body, status_code=status, headers=headers)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", extra={"exc_type": type(exc).__name__})
        status, body, headers = to_http(ChatError(ErrorCode.INTERNAL))
        return JSONResponse(body, status_code=status, headers=headers)


def create_app(
    settings: Settings | None = None,
    *,
    provider: LlmProvider | None = None,
    repo: Repository | None = None,
    enable_ui: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, log_message_content=settings.log_message_content)
    container = build_container(settings, provider=provider, repo=repo)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await container.repo.migrate()
        await asyncio.to_thread(container.index.reload)
        await container.service.cleanup_expired()
        background = [
            asyncio.create_task(_cleanup_loop(container)),
            asyncio.create_task(_verify_model(container)),
        ]
        logger.info(
            "startup complete",
            extra={
                "storage": settings.storage_backend,
                "gemini_configured": container.provider.configured,
                "admin_enabled": settings.admin_api_key is not None,
            },
        )
        try:
            yield
        finally:
            for task in background:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await container.service.drain()

    app = FastAPI(
        title=container.domain.title,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.container = container
    _register_errors(app)
    for router in (chat.router, sessions.router, feedback.router, meta.router, meta.admin):
        app.include_router(router)

    # Added innermost first; the last one added runs first.
    app.add_middleware(
        ClientCookieMiddleware, signer=container.signer, secure=settings.is_production
    )
    origins = settings.allowed_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-Admin-Key", "X-Request-ID"],
        )
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(SecurityHeadersMiddleware, production=settings.is_production)
    app.add_middleware(RequestContextMiddleware)

    if enable_ui:
        from app.ui.page import mount_ui

        mount_ui(app, container)
    return app
