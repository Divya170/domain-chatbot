"""Session endpoints: rehydrate and delete (ownership checked by the service)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import ClientIdDep, ContainerDep
from app.api.schemas import MessageOut

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def session_messages(
    session_id: str, container: ContainerDep, client_id: ClientIdDep
) -> list[MessageOut]:
    messages = await container.service.visible_messages(client_id, session_id)
    return [MessageOut.of(m) for m in messages]


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str, container: ContainerDep, client_id: ClientIdDep
) -> Response:
    await container.service.delete_session(client_id, session_id)
    return Response(status_code=204)
