"""Local, conservative token estimates (no tokenizer download, no API call).

Latin-script text averages about 4 characters per token. Indic scripts tokenise less
efficiently, so they are counted at 2 characters per token. The estimate errs high so
budgets are never exceeded; it is calibrated against real ``usage_metadata`` later.
"""

from __future__ import annotations

import math

LATIN_CHARS_PER_TOKEN = 4.0
OTHER_CHARS_PER_TOKEN = 2.0
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    latin = 0
    other = 0
    for char in text:
        if ord(char) < 0x0250:
            latin += 1
        else:
            other += 1
    return math.ceil(latin / LATIN_CHARS_PER_TOKEN + other / OTHER_CHARS_PER_TOKEN)


def estimate_message_tokens(text: str) -> int:
    """Estimate for one conversation turn, including role/format overhead."""
    return estimate_tokens(text) + MESSAGE_OVERHEAD_TOKENS
