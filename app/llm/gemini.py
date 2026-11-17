"""Gemini adapter (google-genai) with streaming, timeouts, transient retry and usage.

The SDK is imported only in ``create_gemini_client`` so the rest of the app (and the
test suite) runs without it. Requests are passed as plain dicts, which the SDK accepts
for ``contents`` and ``config``.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, Protocol

from app.llm.base import LlmChunk, LlmRequest, Usage
from app.services.errors import ChatError, ErrorCode

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
_NETWORK_ERROR_NAMES = frozenset(
    {"TransportError", "ConnectError", "ReadError", "WriteError", "RemoteProtocolError"}
)


class GeminiClientLike(Protocol):
    """The part of ``google.genai.Client`` this adapter uses."""

    @property
    def aio(self) -> Any: ...


def create_gemini_client(api_key: str, timeout_seconds: float) -> GeminiClientLike:
    from google import genai  # imported lazily on purpose

    client: GeminiClientLike = genai.Client(
        api_key=api_key, http_options={"timeout": int(timeout_seconds * 1000)}
    )
    return client


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, ConnectionError):
        return True
    return any(cls.__name__ in _NETWORK_ERROR_NAMES for cls in type(exc).__mro__)


def map_provider_error(exc: BaseException) -> tuple[ChatError, bool]:
    """Return (typed error, retryable). 429 is never retried."""
    if isinstance(exc, ChatError):
        return exc, False
    if isinstance(exc, TimeoutError):
        return ChatError(ErrorCode.TIMEOUT, detail="provider timeout"), False
    status = _status_code(exc)
    if status == 429:
        return ChatError(ErrorCode.RATE_LIMIT, detail="provider 429", retry_after=30), False
    if status is not None and status >= 500:
        return ChatError(ErrorCode.PROVIDER_DOWN, detail=f"provider {status}"), True
    if status is not None:
        return ChatError(ErrorCode.PROVIDER_DOWN, detail=f"provider {status}"), False
    if _is_network_error(exc):
        return ChatError(ErrorCode.PROVIDER_DOWN, detail=type(exc).__name__), True
    return ChatError(ErrorCode.PROVIDER_DOWN, detail=type(exc).__name__), False


def _usage_from(chunk: Any) -> Usage | None:
    meta = getattr(chunk, "usage_metadata", None)
    if meta is None:
        return None
    prompt = getattr(meta, "prompt_token_count", None) or 0
    output = getattr(meta, "candidates_token_count", None) or 0
    if not prompt and not output:
        return None
    return Usage(input_tokens=int(prompt), output_tokens=int(output))


class GeminiProvider:
    def __init__(
        self,
        client: GeminiClientLike | None,
        model: str | None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._model = model
        self._sleep = sleep

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def configured(self) -> bool:
        return self._client is not None and bool(self._model)

    async def verify_model(self) -> bool:
        """Check once that the configured model id exists. Never raises."""
        if self._client is None or not self._model:
            return False
        try:
            await self._client.aio.models.get(model=self._model)
        except Exception as exc:  # noqa: BLE001 - report only, startup must continue
            logger.warning(
                "gemini model check failed",
                extra={"model": self._model, "error": type(exc).__name__},
            )
            return False
        return True

    def _payload(self, request: LlmRequest) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        contents = [{"role": t.role, "parts": [{"text": t.text}]} for t in request.contents]
        config = {
            "system_instruction": request.system_instruction,
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.thinking_budget is not None:
            config["thinking_config"] = {"thinking_budget": request.thinking_budget}
        return contents, config

    async def stream(self, request: LlmRequest) -> AsyncGenerator[LlmChunk, None]:
        if self._client is None or not self._model:
            raise ChatError(ErrorCode.NOT_CONFIGURED, detail="gemini key or model missing")
        contents, config = self._payload(request)
        attempt = 0
        while True:
            attempt += 1
            yielded = False
            usage: Usage | None = None
            stream: Any = None
            try:
                stream = await asyncio.wait_for(
                    self._client.aio.models.generate_content_stream(
                        model=self._model, contents=contents, config=config
                    ),
                    timeout=request.timeout_seconds,
                )
                iterator = stream.__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            iterator.__anext__(), timeout=request.timeout_seconds
                        )
                    except StopAsyncIteration:
                        break
                    usage = _usage_from(chunk) or usage
                    text = getattr(chunk, "text", None)
                    if text:
                        yielded = True
                        yield LlmChunk(text=text)
                if usage is not None:
                    yield LlmChunk(usage=usage)
                return
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as exc:
                error, retryable = map_provider_error(exc)
                # A first-token timeout is retried once: Gemini sometimes stalls on one call.
                first_token_timeout = error.code is ErrorCode.TIMEOUT and attempt == 1
                if (retryable or first_token_timeout) and not yielded and attempt < MAX_ATTEMPTS:
                    delay = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1) + random.uniform(0, 0.5)  # noqa: S311 - jitter, not crypto
                    logger.warning(
                        "gemini transient error, retrying",
                        extra={
                            "code": error.code.value,
                            "detail": error.detail,
                            "attempt": attempt,
                        },
                    )
                    await self._sleep(delay)
                    continue
                raise error from exc
            finally:
                closer = getattr(stream, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        logger.debug("gemini stream close failed")
