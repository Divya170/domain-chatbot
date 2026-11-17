"""Chat endpoints: streaming, non-streaming and regenerate (one pipeline)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from app.api.deps import ClientIdDep, ClientIpDep, ContainerDep
from app.api.schemas import ChatIn, ChatOut, ErrorOut, RegenerateIn, SourceOut, UsageOut
from app.api.sse import sse_response
from app.services.chat_service import ChunkEvent, DoneEvent, ErrorEvent, SourcesEvent, StartEvent

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(
    body: ChatIn,
    request: Request,
    container: ContainerDep,
    client_id: ClientIdDep,
    ip: ClientIpDep,
) -> StreamingResponse:
    turn = await container.service.prepare_message(
        client_id=client_id,
        ip=ip,
        message=body.message,
        language=body.language,
        session_id=body.session_id,
    )
    return sse_response(container.service.run(turn), request.is_disconnected)


@router.post("/regenerate")
async def chat_regenerate(
    body: RegenerateIn,
    request: Request,
    container: ContainerDep,
    client_id: ClientIdDep,
    ip: ClientIpDep,
) -> StreamingResponse:
    turn = await container.service.prepare_regenerate(
        client_id=client_id, ip=ip, session_id=body.session_id, message_id=body.message_id
    )
    return sse_response(container.service.run(turn), request.is_disconnected)


@router.post("", response_model=ChatOut)
async def chat(
    body: ChatIn, container: ContainerDep, client_id: ClientIdDep, ip: ClientIpDep
) -> JSONResponse:
    turn = await container.service.prepare_message(
        client_id=client_id,
        ip=ip,
        message=body.message,
        language=body.language,
        session_id=body.session_id,
    )
    out = ChatOut(reply="", session_id=turn.session_id)
    parts: list[str] = []
    status = 200
    async for event in container.service.run(turn):
        if isinstance(event, StartEvent):
            out.message_id = event.message_id
        elif isinstance(event, ChunkEvent):
            parts.append(event.text)
        elif isinstance(event, SourcesEvent):
            out.sources = [SourceOut.of(s) for s in event.sources]
        elif isinstance(event, DoneEvent):
            out.latency_ms = event.latency_ms
            if event.usage:
                out.usage = UsageOut(
                    input=event.usage.input_tokens, output=event.usage.output_tokens
                )
        elif isinstance(event, ErrorEvent):
            status = event.error.status
            out.error = ErrorOut(
                code=event.error.code.value,
                message=event.error.public_message,
                retry_after=event.error.retry_after,
            )
    out.reply = "".join(parts).strip()
    headers = (
        {"Retry-After": str(out.error.retry_after)}
        if out.error and out.error.retry_after is not None
        else None
    )
    return JSONResponse(out.model_dump(by_alias=True), status_code=status, headers=headers)
