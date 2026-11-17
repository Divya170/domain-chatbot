"""In-process client the UI uses to call the public API (D2).

Requests go through the full ASGI app (all middleware, routing, validation and rate
limits), exactly like an external client, but without a network hop. Unlike httpx's
``ASGITransport`` the response body streams, and closing a stream sends
``http.disconnect`` so the server aborts the Gemini call ("Stop generating").

The browser's real IP and its signed client cookie are forwarded so rate limits and
session ownership apply to the actual user.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message

from app.security.cookies import COOKIE_NAME


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


@dataclass(slots=True)
class StreamResponse:
    status: int
    headers: dict[str, str]
    chunks: AsyncIterator[bytes]

    async def read(self) -> bytes:
        return b"".join([c async for c in self.chunks])


class _AppError(Exception):
    pass


class AsgiApiClient:
    def __init__(
        self, app: ASGIApp, *, client_ip: str, cookie: str | None, host: str = "localhost"
    ) -> None:
        self.app = app
        self.client_ip = client_ip
        self.cookie = cookie
        self.host = host

    def _scope(self, method: str, path: str, body: bytes) -> dict[str, Any]:
        parts = urlsplit(path)
        headers = [
            (b"host", self.host.encode()),
            (b"accept", b"application/json, text/event-stream"),
            (b"content-length", str(len(body)).encode()),
        ]
        if body:
            headers.append((b"content-type", b"application/json"))
        if self.cookie:
            headers.append((b"cookie", f"{COOKIE_NAME}={self.cookie}".encode()))
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parts.path,
            "raw_path": parts.path.encode(),
            "query_string": parts.query.encode(),
            "root_path": "",
            "headers": headers,
            "client": (self.client_ip, 0),
            "server": (self.host, 80),
            "state": {},
        }

    @asynccontextmanager
    async def stream(
        self, method: str, path: str, payload: Any = None
    ) -> AsyncIterator[StreamResponse]:
        body = json.dumps(payload).encode() if payload is not None else b""
        scope = self._scope(method, path, body)
        disconnect = asyncio.Event()
        sent_request = False
        outbox: asyncio.Queue[Message | BaseException | None] = asyncio.Queue()

        async def receive() -> Message:
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            await outbox.put(message)

        async def run() -> None:
            try:
                await self.app(scope, receive, send)
            except BaseException as exc:  # forwarded to the reader
                await outbox.put(exc)
                if isinstance(exc, asyncio.CancelledError):
                    raise
            finally:
                await outbox.put(None)

        task = asyncio.create_task(run())

        async def next_message() -> Message | None:
            item = await outbox.get()
            if isinstance(item, BaseException):
                raise _AppError(type(item).__name__) from item
            return item

        try:
            start = await next_message()
            if start is None or start["type"] != "http.response.start":
                raise _AppError("application sent no response")
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start["headers"]
            }

            async def chunks() -> AsyncIterator[bytes]:
                while True:
                    message = await next_message()
                    if message is None:
                        return
                    if message["type"] == "http.response.body":
                        data = message.get("body", b"")
                        if data:
                            yield data
                        if not message.get("more_body", False):
                            return

            yield StreamResponse(int(start["status"]), headers, chunks())
        finally:
            disconnect.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except Exception:  # noqa: BLE001 - app errors were already reported
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def request(self, method: str, path: str, payload: Any = None) -> ApiResponse:
        async with self.stream(method, path, payload) as response:
            body = await response.read()
            return ApiResponse(response.status, response.headers, body)


async def parse_sse(chunks: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any]]:
    """Yield the JSON payload of each ``data:`` frame; comments (heartbeats) are skipped."""
    buffer = b""
    async for chunk in chunks:
        buffer += chunk
        while b"\n\n" in buffer:
            block, buffer = buffer.split(b"\n\n", 1)
            data = [line[5:].lstrip() for line in block.split(b"\n") if line.startswith(b"data:")]
            if data:
                yield json.loads(b"\n".join(data))
