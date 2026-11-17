"""Server-Sent Events: typed frames, 15 s heartbeats, abort on client disconnect."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from starlette.responses import StreamingResponse

from app.api.schemas import event_payload
from app.services.chat_service import ChatEvent

HEARTBEAT_SECONDS = 15.0
HEARTBEAT = b": heartbeat\n\n"
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}

_END = object()


def encode_frame(event: ChatEvent) -> bytes:
    data = json.dumps(event_payload(event), ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n".encode()


async def sse_frames(
    events: AsyncIterator[ChatEvent],
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[bytes]:
    """Pump events through a queue so heartbeats never cancel the event stream.

    When the client goes away (or this generator is closed/cancelled) the producer
    task is cancelled, which closes the provider stream (the Gemini call is aborted).
    """
    queue: asyncio.Queue[object] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for event in events:
                await queue.put(event)
        finally:
            queue.put_nowait(_END)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                if await is_disconnected():
                    break
                yield HEARTBEAT
                continue
            if item is _END:
                break
            yield encode_frame(item)  # type: ignore[arg-type]
            if await is_disconnected():
                break
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


def sse_response(
    events: AsyncIterator[ChatEvent], is_disconnected: Callable[[], Awaitable[bool]]
) -> StreamingResponse:
    return StreamingResponse(
        sse_frames(events, is_disconnected),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
