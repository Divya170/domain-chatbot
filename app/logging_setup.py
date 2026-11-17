"""Structured JSON logging with a request id on every line.

Message text is never logged by default (``LOG_MESSAGE_CONTENT=false``); when enabled,
it is redacted first (see ``log_content``).
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.security.redaction import redact

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_log_content = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str, *, log_message_content: bool) -> None:
    global _log_content
    _log_content = log_message_content
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def log_content(text: str) -> str | None:
    """Value to attach to a log line for user/assistant text: None unless enabled."""
    return redact(text)[:500] if _log_content else None
