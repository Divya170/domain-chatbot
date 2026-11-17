"""Thumbs up/down on an assistant message."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import ClientIdDep, ContainerDep
from app.api.schemas import FeedbackIn

router = APIRouter(prefix="/api/messages", tags=["feedback"])


@router.post("/{message_id}/feedback", status_code=204)
async def message_feedback(
    message_id: str, body: FeedbackIn, container: ContainerDep, client_id: ClientIdDep
) -> Response:
    await container.service.feedback(client_id, message_id, body.rating, body.note)
    return Response(status_code=204)
