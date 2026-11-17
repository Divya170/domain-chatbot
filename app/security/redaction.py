"""Redact identity- and phone-like numbers before storage, logging and the model call.

Patterns (D8): Aadhaar-like 12 digits (optionally grouped 4-4-4 with spaces/hyphens),
Indian mobile numbers (optional +91/91/0 prefix, 10 digits starting 6-9, optionally
split 5-5), and any other run of 9 or more digits. Amounts such as "₹5,000" and years
are left alone.
"""

from __future__ import annotations

import re

ID_TOKEN = "[REDACTED-ID]"  # noqa: S105 - not a secret
PHONE_TOKEN = "[REDACTED-PHONE]"  # noqa: S105
NUMBER_TOKEN = "[REDACTED-NUMBER]"  # noqa: S105

_AADHAAR = re.compile(r"(?<![\d])\d{4}[ \-]?\d{4}[ \-]?\d{4}(?![\d])")
_MOBILE = re.compile(r"(?<![\d+])(?:\+?91[ \-]?|0)?[6-9]\d{4}[ \-]?\d{5}(?![\d])")
_LONG_RUN = re.compile(r"(?<![\d])\d{9,}(?![\d])")


def redact(text: str) -> str:
    text = _MOBILE.sub(PHONE_TOKEN, text)  # first, so "+91" + 10 digits is not read as an ID
    text = _AADHAAR.sub(ID_TOKEN, text)
    return _LONG_RUN.sub(NUMBER_TOKEN, text)
