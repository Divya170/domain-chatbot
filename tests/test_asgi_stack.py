"""Middleware, SSE and the in-process API client, tested on a plain Starlette app
(runs without FastAPI installed)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from app.api.middleware import (
    API_CSP,
    PAGE_CSP,
    BodyLimitMiddleware,
    ClientCookieMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.sse import HEARTBEAT, encode_frame, sse_frames, sse_response
from app.llm.fake import FakeLlmProvider
from app.security.cookies import COOKIE_NAME, CookieSigner
from app.services.chat_service import CANCELLED, ChatEvent, ChunkEvent, StartEvent
from app.ui.client import AsgiApiClient, parse_sse
from tests.test_chat_service import make_service

pytestmark = pytest.mark.anyio
SIGNER = CookieSigner("test-secret")


def build(
    endpoints: dict[str, object], *, production: bool = False, max_bytes: int = 1024
) -> ASGIApp:
    async def echo(request: Request) -> Response:
        payload = await request.body()
        return JSONResponse(
            {
                "client": request.client.host if request.client else None,
                "clientId": request.scope["state"]["client_id"],
                "size": len(payload),
            }
        )

    async def page(_: Request) -> Response:
        return HTMLResponse("<p>page</p>")

    routes = [Route("/api/echo", echo, methods=["POST", "GET"]), Route("/", page)]
    routes += [Route(path, fn, methods=["POST", "GET"]) for path, fn in endpoints.items()]  # type: ignore[arg-type]
    app: ASGIApp = Starlette(routes=routes)
    app = ClientCookieMiddleware(app, signer=SIGNER, secure=production)
    app = BodyLimitMiddleware(app, max_bytes=max_bytes)
    app = SecurityHeadersMiddleware(app, production=production)
    return RequestContextMiddleware(app)


class TestMiddleware:
    async def test_cookie_issued_once_and_identity_stable(self) -> None:
        app = build({})
        anon = AsgiApiClient(app, client_ip="9.9.9.9", cookie=None)
        first = await anon.request("GET", "/api/echo")
        set_cookie = first.headers["set-cookie"]
        assert set_cookie.startswith(f"{COOKIE_NAME}=")
        assert (
            "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie and "Secure" not in set_cookie
        )
        value = set_cookie.split(";")[0].split("=", 1)[1]
        known = AsgiApiClient(app, client_ip="9.9.9.9", cookie=value)
        again = await known.request("GET", "/api/echo")
        assert "set-cookie" not in again.headers
        assert again.json()["clientId"] == SIGNER.verify(value) == first.json()["clientId"]
        assert again.json()["client"] == "9.9.9.9"

    async def test_forged_cookie_gets_new_identity(self) -> None:
        app = build({})
        _, value = CookieSigner("other-secret").issue()
        response = await AsgiApiClient(app, client_ip="1.1.1.1", cookie=value).request(
            "GET", "/api/echo"
        )
        assert "set-cookie" in response.headers
        assert response.json()["clientId"] != value.split(".")[0]

    async def test_security_headers(self) -> None:
        app = build({}, production=True)
        client = AsgiApiClient(app, client_ip="1.1.1.1", cookie=None)
        api = await client.request("GET", "/api/echo")
        page = await client.request("GET", "/")
        assert api.headers["content-security-policy"] == API_CSP
        assert page.headers["content-security-policy"] == PAGE_CSP
        for r in (api, page):
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-frame-options"] == "DENY"
            assert "max-age" in r.headers["strict-transport-security"]
            assert "Secure" in r.headers["set-cookie"]
        dev = await AsgiApiClient(build({}), client_ip="1.1.1.1", cookie=None).request("GET", "/")
        assert "strict-transport-security" not in dev.headers

    async def test_request_id(self) -> None:
        response = await AsgiApiClient(build({}), client_ip="1", cookie=None).request("GET", "/")
        assert len(response.headers["x-request-id"]) == 32

    async def test_body_limit(self) -> None:
        client = AsgiApiClient(build({}, max_bytes=100), client_ip="1", cookie=None)
        ok = await client.request("POST", "/api/echo", {"m": "x" * 50})
        assert ok.status == 200
        too_big = await client.request("POST", "/api/echo", {"m": "x" * 200})
        assert too_big.status == 413
        assert too_big.json()["error"]["code"] == "INVALID_INPUT"


class TestSse:
    async def test_frames_and_heartbeat(self) -> None:
        async def events() -> AsyncIterator[ChatEvent]:
            yield StartEvent("m1", "s1")
            await asyncio.sleep(0.05)
            yield ChunkEvent("hé")

        async def connected() -> bool:
            return False

        frames = [f async for f in sse_frames(events(), connected, heartbeat=0.02)]
        assert frames[0] == b'data: {"type":"start","messageId":"m1","sessionId":"s1"}\n\n'
        assert HEARTBEAT in frames
        assert frames[-1] == encode_frame(ChunkEvent("hé"))
        assert json.loads(frames[-1][6:]) == {"type": "chunk", "text": "hé"}

    async def test_stop_aborts_provider_end_to_end(self) -> None:
        fake = FakeLlmProvider(" ".join(f"w{i}" for i in range(50)), delay_seconds=0.02)
        service, repo, _ = make_service(fake)

        async def stream(request: Request) -> Response:
            turn = await service.prepare_message(
                client_id="c", ip="ip", message="hello", language="en", session_id=None
            )
            request.state.session_id = turn.session_id
            stream.session_id = turn.session_id  # type: ignore[attr-defined]
            return sse_response(service.run(turn), request.is_disconnected)

        app = build({"/api/stream": stream})
        client = AsgiApiClient(app, client_ip="1", cookie=None)
        frames = []
        async with client.stream("POST", "/api/stream", {}) as response:
            assert response.status == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-accel-buffering"] == "no"
            async for frame in parse_sse(response.chunks):
                frames.append(frame)
                if len(frames) == 3:
                    break  # user pressed Stop
        await asyncio.sleep(0.1)
        await service.drain()
        assert [f["type"] for f in frames] == ["start", "chunk", "chunk"]
        assert fake.closed_early
        stored = await repo.list_messages(stream.session_id)  # type: ignore[attr-defined]
        assert stored[-1].error_code == CANCELLED
        assert len(stored[-1].content.split()) < 50


async def test_parse_sse_handles_split_chunks_and_comments() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'data: {"type":"st'
        yield b'art"}\n\n: heartbeat\n\ndata: {"type":"done"}\n'
        yield b"\n"

    assert [f async for f in parse_sse(chunks())] == [{"type": "start"}, {"type": "done"}]


async def test_client_reports_app_crash() -> None:
    async def broken(scope, receive, send):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    client = AsgiApiClient(broken, client_ip="1", cookie=None)
    with pytest.raises(Exception, match="RuntimeError"):
        await client.request("GET", "/")
