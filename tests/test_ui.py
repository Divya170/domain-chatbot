"""UI smoke tests (need nicegui, fastapi and pydantic-settings; skipped otherwise).

NiceGUI can be mounted once per process, so one app serves the whole module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")

from app.config import Settings
from app.domain import DOMAIN
from app.llm.fake import FakeLlmProvider
from app.main import create_app
from app.ui.client import AsgiApiClient
from tests.conftest import KB_ROOT

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def ui_app() -> AsyncIterator[Any]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        knowledge_dir=KB_ROOT,
        storage_backend="memory",
        session_secret="test-secret",
        kb_reload_check_seconds=0,
    )
    app = create_app(settings, provider=FakeLlmProvider("Hi"), enable_ui=True)
    async with app.router.lifespan_context(app):
        yield app


async def test_page_loads_with_welcome_and_starters(ui_app: Any) -> None:
    client = AsgiApiClient(ui_app, client_ip="1.2.3.4", cookie=None)
    response = await client.request("GET", "/")
    assert response.status == 200
    page = response.body.decode()
    assert DOMAIN.title in page
    assert DOMAIN.starters["en"][0] in page  # from /api/config
    assert "set-cookie" in response.headers
    assert "unsafe-eval" in response.headers["content-security-policy"]


async def test_api_still_reachable_with_ui_mounted(ui_app: Any) -> None:
    client = AsgiApiClient(ui_app, client_ip="1.2.3.4", cookie=None)
    assert (await client.request("GET", "/api/config")).status == 200
    assert (await client.request("GET", "/health")).status == 200
