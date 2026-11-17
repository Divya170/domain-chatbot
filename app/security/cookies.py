"""Signed client-id cookie (D1). Standard library only (HMAC-SHA256).

The cookie carries an opaque random client id plus its signature. Sessions belong to
the client id; every session read/delete/regenerate/feedback checks ownership.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

logger = logging.getLogger(__name__)

COOKIE_NAME = "cb_client"
COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # browsers cap at ~400 days


class CookieSigner:
    def __init__(self, secret: str | None) -> None:
        self.configured = bool(secret)
        if not secret:
            logger.warning("SESSION_SECRET is not set; using a temporary secret for this process")
            secret = secrets.token_urlsafe(32)
        self._key = hashlib.sha256(b"cb-client-cookie:" + secret.encode()).digest()

    def _sign(self, value: str) -> str:
        digest = hmac.new(self._key, value.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest[:24]).decode().rstrip("=")

    def issue(self) -> tuple[str, str]:
        """Return (client_id, cookie_value)."""
        client_id = secrets.token_urlsafe(18)
        return client_id, f"{client_id}.{self._sign(client_id)}"

    def verify(self, cookie_value: str | None) -> str | None:
        """Return the client id if the signature is valid, else None."""
        if not cookie_value or cookie_value.count(".") != 1:
            return None
        client_id, signature = cookie_value.split(".")
        if not client_id or not hmac.compare_digest(signature, self._sign(client_id)):
            return None
        return client_id
