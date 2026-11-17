"""HTTP API tests (need fastapi, httpx and pydantic-settings; skipped otherwise)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("pydantic_settings")

from app.api.middleware import API_CSP  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm.fake import FakeLlmProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security.cookies import COOKIE_NAME  # noqa: E402
from app.services.errors import ChatError, ErrorCode  # noqa: E402
from app.ui.client import AsgiApiClient, parse_sse  # noqa: E402
from tests.conftest import KB_ROOT  # noqa: E402

pytestmark = pytest.mark.anyio


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "knowledge_dir": KB_ROOT,
        "storage_backend": "memory",
        "session_secret": "test-secret",
        "rate_limit_per_minute": 100,
        "kb_reload_check_seconds": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


class Api:
    def __init__(self, app: Any, client: Any, fake: FakeLlmProvider) -> None:
        self.app = app
        self.client = client
        self.fake = fake

    async def stream(
        self, payload: dict[str, Any], path: str = "/api/chat/stream", client: Any = None
    ) -> tuple[Any, list[dict[str, Any]]]:
        response = await (client or self.client).post(path, json=payload)
        frames = [
            json.loads(line[5:]) for line in response.text.split("\n") if line.startswith("data:")
        ]
        return response, frames


async def open_api(fake: FakeLlmProvider | None = None, **overrides: Any) -> AsyncIterator[Api]:
    fake = fake or FakeLlmProvider("Hello **world**")
    app = create_app(make_settings(**overrides), provider=fake, enable_ui=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Api(app, client, fake)


@pytest.fixture
async def api() -> AsyncIterator[Api]:
    async for value in open_api():
        yield value


async def test_stream_frame_order_and_headers(api: Api) -> None:
    response, frames = await api.stream({"message": "PMMVY documents?", "language": "en"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    types = [f["type"] for f in frames]
    assert types[0] == "start" and types[-2:] == ["sources", "done"]
    assert set(types[1:-2]) == {"chunk"}
    assert "".join(f["text"] for f in frames if f["type"] == "chunk") == "Hello **world**"
    assert frames[0]["messageId"] == frames[-1]["messageId"]
    assert frames[-1]["usage"]["output"] > 0
    assert any(s["file"] == "schemes/pmmvy.md" for s in frames[-2]["sources"])


async def test_cookie_flags_and_security_headers(api: Api) -> None:
    response = await api.client.get("/api/config")
    cookie = response.headers["set-cookie"]
    assert (
        cookie.startswith(f"{COOKIE_NAME}=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    )
    assert response.headers["content-security-policy"] == API_CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "x-request-id" in response.headers


async def test_non_streaming_chat_and_legacy_body(api: Api) -> None:
    response = await api.client.post(
        "/api/chat", json={"message": "hi", "language": "தமிழ் (Tamil)", "history": [{"x": 1}]}
    )
    body = response.json()
    assert response.status_code == 200
    assert body["reply"] == "Hello **world**"
    assert body["messageId"] and body["sessionId"]
    assert "Tamil" in api.fake.requests[-1].system_instruction


@pytest.mark.parametrize(
    "payload", [{"message": ""}, {"message": "   "}, {"message": "x" * 4001}, {}, {"message": 5}]
)
async def test_validation(api: Api, payload: dict[str, Any]) -> None:
    response = await api.client.post("/api/chat/stream", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


async def test_unknown_language_defaults_to_english(api: Api) -> None:
    response, _ = await api.stream({"message": "hi", "language": "xx"})
    assert response.status_code == 200
    assert "English" in api.fake.requests[-1].system_instruction


async def test_oversized_body_413(api: Api) -> None:
    response = await api.client.post(
        "/api/chat", content=b"x" * (300 * 1024), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413


async def test_rehydrate_ownership_and_delete(api: Api) -> None:
    _, frames = await api.stream({"message": "hello"})
    session_id = frames[0]["sessionId"]
    messages = (await api.client.get(f"/api/sessions/{session_id}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["id"] == frames[0]["messageId"]

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as stranger:
        assert (await stranger.get(f"/api/sessions/{session_id}/messages")).status_code == 404
        assert (await stranger.delete(f"/api/sessions/{session_id}")).status_code == 404
        _, other = await api.stream({"message": "hi", "sessionId": session_id}, client=stranger)
        assert other == []  # 404 JSON, no stream

    assert (await api.client.delete(f"/api/sessions/{session_id}")).status_code == 204
    assert (await api.client.get(f"/api/sessions/{session_id}/messages")).status_code == 404


async def test_feedback(api: Api) -> None:
    _, frames = await api.stream({"message": "hello"})
    message_id = frames[0]["messageId"]
    ok = await api.client.post(f"/api/messages/{message_id}/feedback", json={"rating": "up"})
    assert ok.status_code == 204
    bad = await api.client.post(f"/api/messages/{message_id}/feedback", json={"rating": "meh"})
    assert bad.status_code == 422
    missing = await api.client.post("/api/messages/nope/feedback", json={"rating": "up"})
    assert missing.status_code == 404


async def test_regenerate(api: Api) -> None:
    _, frames = await api.stream({"message": "hello"})
    api.fake.reply = "Second try"
    response, regen = await api.stream(
        {"sessionId": frames[0]["sessionId"], "messageId": frames[0]["messageId"]},
        path="/api/chat/regenerate",
    )
    assert response.status_code == 200 and regen[-1]["type"] == "done"
    messages = (await api.client.get(f"/api/sessions/{frames[0]['sessionId']}/messages")).json()
    assert [m["content"] for m in messages] == ["hello", "Second try"]


async def test_config_and_health(api: Api) -> None:
    config = (await api.client.get("/api/config")).json()
    assert config["defaultLanguage"] == "en" and len(config["languages"]) == 2
    assert (
        set(config["welcome"])
        == set(config["starters"])
        == {lang["code"] for lang in config["languages"]}
    )
    health = (await api.client.get("/health")).json()
    assert health["knowledgeLoaded"] and health["knowledgeFiles"] == 8
    assert health["storageOk"] and health["geminiConfigured"] and health["sessionSecretConfigured"]
    assert "gemini-" not in json.dumps(health)  # never reveals the model id


async def test_admin_endpoints_hidden_without_key(api: Api) -> None:
    assert (await api.client.get("/api/admin/kb/status")).status_code == 404
    assert (await api.client.post("/api/admin/kb/reload")).status_code == 404


async def test_admin_endpoints_with_key() -> None:
    async for api in open_api(admin_api_key="admin-secret"):
        assert (await api.client.get("/api/admin/kb/status")).status_code == 401
        wrong = await api.client.get("/api/admin/kb/status", headers={"X-Admin-Key": "nope"})
        assert wrong.status_code == 403
        ok = await api.client.post("/api/admin/kb/reload", headers={"X-Admin-Key": "admin-secret"})
        assert ok.status_code == 200
        status = ok.json()
        assert status["files"] == 8 and len(status["sampleFiles"]) == 8


async def test_not_configured_is_typed() -> None:
    async for api in open_api(FakeLlmProvider(configured=False)):
        response = await api.client.post("/api/chat/stream", json={"message": "hi"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "NOT_CONFIGURED"


async def test_provider_failure_fallback_and_429() -> None:
    fake = FakeLlmProvider("x", fail_with=ChatError(ErrorCode.RATE_LIMIT, retry_after=30))
    async for api in open_api(fake):
        _, frames = await api.stream({"message": "hi", "language": "ta"})
        assert frames[-1] == {
            "type": "error",
            "code": "RATE_LIMIT",
            "message": frames[-1]["message"],
            "retryAfter": 30,
        }
        text = "".join(f["text"] for f in frames if f["type"] == "chunk")
        assert "மன்னிக்கவும்" in text  # Tamil fallback


async def test_rate_limit_returns_retry_after() -> None:
    async for api in open_api(rate_limit_per_minute=1):
        first, _ = await api.stream({"message": "one"})
        assert first.status_code == 200
        second = await api.client.post("/api/chat/stream", json={"message": "two"})
        assert second.status_code == 429
        assert int(second.headers["retry-after"]) > 0
        assert second.json()["error"]["code"] == "RATE_LIMIT"


async def test_client_disconnect_aborts_provider() -> None:
    fake = FakeLlmProvider(" ".join(f"w{i}" for i in range(80)), delay_seconds=0.02)
    async for api in open_api(fake):
        client = AsgiApiClient(api.app, client_ip="5.5.5.5", cookie=None)
        seen = 0
        async with client.stream("POST", "/api/chat/stream", {"message": "hi"}) as response:
            async for _frame in parse_sse(response.chunks):
                seen += 1
                if seen == 3:
                    break
        await api.app.state.container.service.drain()
        assert fake.closed_early
