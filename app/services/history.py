"""Token-budgeted history window built from stored messages (never from the browser).

``max_messages`` counts message rows (user + assistant), like the old
``MAX_HISTORY_MESSAGES = 12``. Only whole turns are kept, so an odd cap rounds down.
A turn is kept only if its user message and exactly one live assistant reply are
both free of errors; failed, cancelled and superseded turns are left out entirely.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.llm.base import Turn
from app.services.tokens import estimate_message_tokens


class HistoryItem(Protocol):
    @property
    def role(self) -> str: ...
    @property
    def content(self) -> str: ...
    @property
    def error_code(self) -> str | None: ...
    @property
    def superseded(self) -> bool: ...


@dataclass
class _TurnBuilder:
    user: HistoryItem
    replies: list[HistoryItem] = field(default_factory=list)

    def valid_pair(self) -> tuple[Turn, Turn] | None:
        if self.user.error_code:
            return None
        live = [r for r in self.replies if not r.superseded]
        if len(live) != 1 or live[0].error_code or not live[0].content.strip():
            return None
        return Turn("user", self.user.content), Turn("model", live[0].content)


def build_turns(messages: Sequence[HistoryItem]) -> list[tuple[Turn, Turn]]:
    """Group chronologically ordered messages into valid (user, model) pairs."""
    builders: list[_TurnBuilder] = []
    for message in messages:
        if message.role == "user":
            builders.append(_TurnBuilder(message))
        elif message.role == "assistant" and builders:
            builders[-1].replies.append(message)
    return [pair for b in builders if (pair := b.valid_pair()) is not None]


def trim_pairs(pairs: list[tuple[Turn, Turn]], token_budget: int) -> list[tuple[Turn, Turn]]:
    """Drop the oldest pairs until the rest fit the budget."""
    kept = list(pairs)
    total = sum(estimate_message_tokens(u.text) + estimate_message_tokens(m.text) for u, m in kept)
    while kept and total > token_budget:
        u, m = kept.pop(0)
        total -= estimate_message_tokens(u.text) + estimate_message_tokens(m.text)
    return kept


def select_history(
    messages: Sequence[HistoryItem], *, max_messages: int, token_budget: int
) -> list[tuple[Turn, Turn]]:
    max_pairs = max_messages // 2
    if max_pairs <= 0 or token_budget <= 0:
        return []
    pairs = build_turns(messages)[-max_pairs:]
    return trim_pairs(pairs, token_budget)


def recent_user_texts(pairs: Sequence[tuple[Turn, Turn]], count: int = 3) -> list[str]:
    """Last few user questions, used to improve retrieval for short follow-ups."""
    return [u.text for u, _ in pairs[-count:]]
