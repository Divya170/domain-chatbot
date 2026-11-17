"""Public config, health and the protected knowledge admin endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import ContainerDep, require_admin

router = APIRouter(tags=["meta"])
admin = APIRouter(prefix="/api/admin/kb", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/api/config")
async def public_config(container: ContainerDep) -> dict[str, Any]:
    d = container.domain
    return {
        "title": d.title,
        "subtitle": d.subtitle,
        "defaultLanguage": d.default_language,
        "languages": [{"code": lang.code, "label": lang.label} for lang in d.languages],
        "welcome": dict(d.welcome),
        "starters": {code: list(items) for code, items in d.starters.items()},
        "disclaimer": dict(d.disclaimer),
        "emergency": dict(d.emergency),
        "maxMessageLength": container.settings.max_message_length,
        "persistentHistory": container.settings.storage_backend == "sqlite",
    }


@router.get("/health")
async def health(container: ContainerDep) -> dict[str, Any]:
    snapshot = await asyncio.to_thread(container.index.snapshot)
    storage_ok = await container.repo.ping()
    return {
        "status": "ok",
        "geminiConfigured": container.provider.configured,
        "geminiModelVerified": container.model_verified,
        "knowledgeLoaded": snapshot is not None,
        "knowledgeFiles": len(snapshot.files) if snapshot else 0,
        "storageOk": storage_ok,
        "sessionSecretConfigured": container.signer.configured,
    }


def _status(container: ContainerDep) -> dict[str, Any]:
    snapshot = container.index.snapshot()
    if snapshot is None:
        return {
            "loaded": False,
            "lastError": container.index.last_error,
            "files": 0,
            "chunks": 0,
            "problems": [],
            "sampleFiles": [],
        }
    return {
        "loaded": True,
        "version": snapshot.version,
        "loadedAt": snapshot.loaded_at.isoformat(),
        "files": len(snapshot.files),
        "chunks": len(snapshot.chunks),
        "problems": [
            {"file": p.file, "message": p.message, "severity": p.severity}
            for p in snapshot.problems
        ],
        "sampleFiles": [f.file for f in snapshot.files if f.status == "sample"],
        "lastError": container.index.last_error,
    }


@admin.post("/reload")
async def kb_reload(container: ContainerDep) -> dict[str, Any]:
    await asyncio.to_thread(container.index.reload)
    return _status(container)


@admin.get("/status")
async def kb_status(container: ContainerDep) -> dict[str, Any]:
    return await asyncio.to_thread(_status, container)
