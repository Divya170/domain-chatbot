from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.llm.base import LlmChunk, LlmRequest, Turn, Usage
from app.llm.fake import FakeLlmProvider
from app.llm.gemini import GeminiProvider, map_provider_error
from app.services.errors import ChatError, ErrorCode

pytestmark = pytest.mark.anyio

REQUEST = LlmRequest(
    system_instruction="sys",
    contents=(Turn("user", "hello"),),
    temperature=0.25,
    max_output_tokens=100,
    timeout_seconds=0.2,
)


async def collect(stream: AsyncIterator[LlmChunk]) -> tuple[str, Usage | None]:
    text, usage = "", None
    async for chunk in stream:
        text += chunk.text
        usage = chunk.usage or usage
    return text, usage


# ---------------------------------------------------------------- fake provider
async def test_fake_streams_text_and_usage() -> None:
    fake = FakeLlmProvider("one two three")
    text, usage = await collect(fake.stream(REQUEST))
    assert text == "one two three"
    assert usage is not None and usage.output_tokens > 0
    assert fake.requests == [REQUEST]
    assert not fake.closed_early


async def test_fake_failure_and_early_close() -> None:
    failing = FakeLlmProvider(
        "a b c", fail_with=ChatError(ErrorCode.PROVIDER_DOWN), fail_after_chunks=1
    )
    with pytest.raises(ChatError):
        await collect(failing.stream(REQUEST))
    fake = FakeLlmProvider("a b c d")
    stream = fake.stream(REQUEST)
    await stream.__anext__()  # type: ignore[attr-defined]
    await stream.aclose()  # type: ignore[attr-defined]
    assert fake.closed_early


# ---------------------------------------------------------------- gemini adapter
class ApiError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"api error {code}")
        self.code = code


class TransportError(Exception):
    """Same class name as httpx.TransportError."""


class Meta:
    def __init__(self, prompt: int, output: int) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = output


class Chunk:
    def __init__(self, text: str | None, meta: Meta | None = None) -> None:
        self.text = text
        self.usage_metadata = meta


class Stream:
    def __init__(
        self,
        chunks: list[Chunk],
        *,
        stall_at: int | None = None,
        fail_at: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.chunks = chunks
        self.stall_at = stall_at
        self.fail_at = fail_at
        self.error = error
        self.closed = False
        self.i = 0

    def __aiter__(self) -> Stream:
        return self

    async def __anext__(self) -> Chunk:
        if self.stall_at == self.i:
            await asyncio.sleep(10)
        if self.fail_at == self.i and self.error is not None:
            raise self.error
        if self.i >= len(self.chunks):
            raise StopAsyncIteration
        self.i += 1
        return self.chunks[self.i - 1]

    async def aclose(self) -> None:
        self.closed = True


class Models:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []
        self.get_error: BaseException | None = None

    async def generate_content_stream(self, **kwargs: Any) -> Stream:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, Stream)
        return outcome

    async def get(self, *, model: str) -> object:
        if self.get_error:
            raise self.get_error
        return object()


class Client:
    def __init__(self, outcomes: list[Any]) -> None:
        self.models = Models(outcomes)

    @property
    def aio(self) -> Client:
        return self


async def no_sleep(_: float) -> None:
    return None


def provider(outcomes: list[Any]) -> tuple[GeminiProvider, Client]:
    client = Client(outcomes)
    return GeminiProvider(client, "gemini-test-model", sleep=no_sleep), client


async def test_gemini_streams_text_usage_and_payload() -> None:
    stream = Stream([Chunk("Hel"), Chunk("lo"), Chunk(None, Meta(12, 3))])
    gem, client = provider([stream])
    text, usage = await collect(gem.stream(REQUEST))
    assert text == "Hello"
    assert usage == Usage(12, 3)
    call = client.models.calls[0]
    assert call["model"] == "gemini-test-model"
    assert call["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert call["config"] == {
        "system_instruction": "sys",
        "temperature": 0.25,
        "max_output_tokens": 100,
    }
    assert stream.closed


async def test_gemini_not_configured() -> None:
    gem = GeminiProvider(None, None)
    assert not gem.configured
    with pytest.raises(ChatError) as info:
        await collect(gem.stream(REQUEST))
    assert info.value.code is ErrorCode.NOT_CONFIGURED
    assert await gem.verify_model() is False


async def test_gemini_429_is_not_retried() -> None:
    gem, client = provider([ApiError(429), Stream([Chunk("never")])])
    with pytest.raises(ChatError) as info:
        await collect(gem.stream(REQUEST))
    assert info.value.code is ErrorCode.RATE_LIMIT
    assert info.value.retry_after is not None
    assert len(client.models.calls) == 1


async def test_gemini_5xx_and_network_errors_are_retried() -> None:
    gem, client = provider([ApiError(503), TransportError(), Stream([Chunk("ok")])])
    text, _ = await collect(gem.stream(REQUEST))
    assert text == "ok"
    assert len(client.models.calls) == 3


async def test_gemini_gives_up_after_max_attempts() -> None:
    gem, client = provider([ApiError(500), ApiError(502), ApiError(503)])
    with pytest.raises(ChatError) as info:
        await collect(gem.stream(REQUEST))
    assert info.value.code is ErrorCode.PROVIDER_DOWN
    assert len(client.models.calls) == 3


async def test_gemini_no_retry_after_text_was_streamed() -> None:
    stream = Stream([Chunk("partial")], fail_at=1, error=ApiError(503))
    gem, client = provider([stream, Stream([Chunk("dup")])])
    received: list[str] = []
    with pytest.raises(ChatError):
        async for chunk in gem.stream(REQUEST):
            received.append(chunk.text)
    assert received == ["partial"]
    assert len(client.models.calls) == 1


async def test_gemini_timeout_between_chunks() -> None:
    gem, _ = provider([Stream([Chunk("a"), Chunk("b")], stall_at=1)])
    with pytest.raises(ChatError) as info:
        await collect(gem.stream(REQUEST))
    assert info.value.code is ErrorCode.TIMEOUT


async def test_gemini_first_token_timeout_is_retried_once() -> None:
    gem, client = provider([Stream([Chunk("x")], stall_at=0), Stream([Chunk("ok")])])
    text, _ = await collect(gem.stream(REQUEST))
    assert text == "ok"
    assert len(client.models.calls) == 2


async def test_gemini_first_token_timeout_twice_gives_up() -> None:
    gem, client = provider([Stream([Chunk("x")], stall_at=0), Stream([Chunk("x")], stall_at=0)])
    with pytest.raises(ChatError) as info:
        await collect(gem.stream(REQUEST))
    assert info.value.code is ErrorCode.TIMEOUT
    assert len(client.models.calls) == 2


async def test_gemini_thinking_budget_is_sent_when_set() -> None:
    request = LlmRequest(
        system_instruction="sys",
        contents=(Turn("user", "hello"),),
        temperature=0.25,
        max_output_tokens=100,
        timeout_seconds=0.2,
        thinking_budget=0,
    )
    gem, client = provider([Stream([Chunk("ok")])])
    await collect(gem.stream(request))
    assert client.models.calls[0]["config"]["thinking_config"] == {"thinking_budget": 0}


async def test_gemini_consumer_close_closes_stream() -> None:
    stream = Stream([Chunk("a"), Chunk("b"), Chunk("c")])
    gem, _ = provider([stream])
    agen = gem.stream(REQUEST)
    await agen.__anext__()  # type: ignore[attr-defined]
    await agen.aclose()  # type: ignore[attr-defined]
    assert stream.closed


async def test_gemini_verify_model() -> None:
    gem, client = provider([])
    assert await gem.verify_model() is True
    client.models.get_error = ApiError(404)
    assert await gem.verify_model() is False


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (ApiError(429), ErrorCode.RATE_LIMIT, False),
        (ApiError(500), ErrorCode.PROVIDER_DOWN, True),
        (ApiError(400), ErrorCode.PROVIDER_DOWN, False),
        (ConnectionResetError(), ErrorCode.PROVIDER_DOWN, True),
        (TimeoutError(), ErrorCode.TIMEOUT, False),
        (ValueError("x"), ErrorCode.PROVIDER_DOWN, False),
        (ChatError(ErrorCode.INTERNAL), ErrorCode.INTERNAL, False),
    ],
)
def test_map_provider_error(exc: BaseException, code: ErrorCode, retryable: bool) -> None:
    error, can_retry = map_provider_error(exc)
    assert error.code is code
    assert can_retry is retryable
