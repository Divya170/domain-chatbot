from __future__ import annotations

from dataclasses import dataclass

from app.services.history import build_turns, recent_user_texts, select_history


@dataclass
class Msg:
    role: str
    content: str
    error_code: str | None = None
    superseded: bool = False


def convo(n: int) -> list[Msg]:
    out: list[Msg] = []
    for i in range(n):
        out += [Msg("user", f"q{i}"), Msg("assistant", f"a{i}")]
    return out


def texts(pairs) -> list[str]:  # type: ignore[no-untyped-def]
    return [t.text for pair in pairs for t in pair]


def test_exact_message_cap_counts_rows() -> None:
    pairs = select_history(convo(10), max_messages=12, token_budget=10_000)
    assert len(pairs) == 6
    assert texts(pairs)[:2] == ["q4", "a4"]
    assert texts(pairs)[-2:] == ["q9", "a9"]
    assert [t.role for t in pairs[0]] == ["user", "model"]


def test_odd_cap_rounds_down_to_whole_turns() -> None:
    assert len(select_history(convo(10), max_messages=5, token_budget=10_000)) == 2


def test_zero_cap_or_budget() -> None:
    assert select_history(convo(3), max_messages=0, token_budget=10_000) == []
    assert select_history(convo(3), max_messages=12, token_budget=0) == []


def test_failed_cancelled_and_superseded_excluded() -> None:
    messages = [
        Msg("user", "ok1"),
        Msg("assistant", "r1"),
        Msg("user", "failed"),
        Msg("assistant", "", error_code="PROVIDER_DOWN"),
        Msg("user", "cancelled"),
        Msg("assistant", "partial", error_code="CANCELLED"),
        Msg("user", "regen"),
        Msg("assistant", "old", superseded=True),
        Msg("assistant", "new"),
        Msg("user", "redacted-user-error", error_code="INTERNAL"),
        Msg("assistant", "x"),
        Msg("user", "no reply yet"),
        Msg("user", "two live"),
        Msg("assistant", "a"),
        Msg("assistant", "b"),
    ]
    assert texts(build_turns(messages)) == ["ok1", "r1", "regen", "new"]


def test_orphan_assistant_message_ignored() -> None:
    messages = [Msg("assistant", "welcome"), Msg("user", "q"), Msg("assistant", "a")]
    assert texts(build_turns(messages)) == ["q", "a"]


def test_token_trimming_drops_oldest_first() -> None:
    messages = convo(4)
    messages[1].content = "long " * 400  # oldest answer is huge
    pairs = select_history(messages, max_messages=12, token_budget=100)
    assert texts(pairs) == ["q1", "a1", "q2", "a2", "q3", "a3"]


def test_recent_user_texts() -> None:
    pairs = build_turns(convo(5))
    assert recent_user_texts(pairs) == ["q2", "q3", "q4"]
