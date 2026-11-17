"""Request dependencies: container, client identity, client IP, admin key."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.container import Container
from app.services.errors import ChatError, ErrorCode


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_client_id(request: Request) -> str:
    client_id = request.scope.get("state", {}).get("client_id")
    if not isinstance(client_id, str):
        raise ChatError(ErrorCode.INTERNAL, detail="client cookie middleware missing")
    return client_id


def get_client_ip(request: Request) -> str:
    # Behind a proxy, Uvicorn's --proxy-headers/--forwarded-allow-ips set this correctly.
    return request.client.host if request.client else "unknown"


def require_admin(
    container: Annotated[Container, Depends(get_container)],
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    configured = container.settings.admin_api_key
    if configured is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if not x_admin_key:
        raise HTTPException(status_code=401, detail="Admin key required")
    if not hmac.compare_digest(
        x_admin_key.encode("utf-8"), configured.get_secret_value().encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


ContainerDep = Annotated[Container, Depends(get_container)]
ClientIdDep = Annotated[str, Depends(get_client_id)]
ClientIpDep = Annotated[str, Depends(get_client_ip)]
