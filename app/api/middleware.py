"""Pure-ASGI middleware (no BaseHTTPMiddleware, so streaming and disconnects work).

* RequestContextMiddleware: request id (``X-Request-ID``) + one access log line.
* SecurityHeadersMiddleware: CSP (strict for API, NiceGUI-compatible for pages), nosniff,
  referrer policy, frame denial, HSTS in production.
* BodyLimitMiddleware: 413 for bodies over the limit (Content-Length and streamed).
* ClientCookieMiddleware: signed HttpOnly client-id cookie (D1).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import MutableMapping
from typing import Any

from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_setup import request_id_var
from app.security.cookies import COOKIE_MAX_AGE, COOKIE_NAME, CookieSigner

logger = logging.getLogger("app.access")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-]{1,64}$")
API_CSP = "default-src 'none'; frame-ancestors 'none'"
# NiceGUI (Vue + Quasar) needs inline and eval'd scripts; see PLAN.md §7.
PAGE_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
    "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


def _set_header(message: MutableMapping[str, Any], name: str, value: str) -> None:
    headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
    lname = name.lower().encode()
    if any(k.lower() == lname for k, _ in headers):
        return
    headers.append((lname, value.encode("latin-1")))
    message["headers"] = headers


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, b"x-request-id")
        request_id = incoming if incoming and _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.monotonic()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                _set_header(message, "X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = str(scope.get("path", ""))
            if not path.startswith("/_nicegui"):
                logger.info(
                    "request",
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": status,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
            request_id_var.reset(token)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        api = path.startswith("/api") or path == "/health"

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                _set_header(message, "Content-Security-Policy", API_CSP if api else PAGE_CSP)
                _set_header(message, "X-Content-Type-Options", "nosniff")
                _set_header(message, "Referrer-Policy", "strict-origin-when-cross-origin")
                _set_header(message, "X-Frame-Options", "DENY")
                _set_header(
                    message, "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
                )
                if self.production:
                    _set_header(
                        message, "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps(
            {"error": {"code": "INVALID_INPUT", "message": "The request is too large."}}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = _header(scope, b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            await self._reject(send)
            return
        received = 0
        started = False

        async def receive_wrapper() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except _BodyTooLarge:
            if not started:
                await self._reject(send)


class ClientCookieMiddleware:
    def __init__(self, app: ASGIApp, *, signer: CookieSigner, secure: bool) -> None:
        self.app = app
        self.signer = signer
        self.secure = secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cookies = cookie_parser(_header(scope, b"cookie") or "")
        value = cookies.get(COOKIE_NAME)
        client_id = self.signer.verify(value)
        issue = client_id is None
        if client_id is None:
            client_id, value = self.signer.issue()
        state = scope.setdefault("state", {})
        state["client_id"] = client_id
        state["client_cookie"] = value

        if not issue:
            await self.app(scope, receive, send)
            return

        cookie = (
            f"{COOKIE_NAME}={value}; Path=/; Max-Age={COOKIE_MAX_AGE}; HttpOnly; SameSite=Lax"
            + ("; Secure" if self.secure else "")
        )

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", cookie.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
