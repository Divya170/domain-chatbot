from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime

import pytest

from app.logging_setup import JsonFormatter, configure_logging, log_content, request_id_var
from app.security.cookies import CookieSigner
from app.security.rate_limit import RateLimiter, SlidingWindow
from app.security.redaction import ID_TOKEN, NUMBER_TOKEN, PHONE_TOKEN, redact
from app.services.errors import ChatError, ErrorCode

pytestmark = pytest.mark.anyio


class TestRedaction:
    @pytest.mark.parametrize("text", ["1234 5678 9012", "1234-5678-9012", "123456789012"])
    def test_aadhaar_like(self, text: str) -> None:
        assert redact(f"my aadhaar is {text}.") == f"my aadhaar is {ID_TOKEN}."

    @pytest.mark.parametrize(
        "text",
        [
            "9876543210",
            "+91 9876543210",
            "+919876543210",
            "09876543210",
            "98765 43210",
            "91-9876543210",
        ],
    )
    def test_mobile_numbers(self, text: str) -> None:
        assert redact(f"call {text} now") == f"call {PHONE_TOKEN} now"

    def test_long_digit_runs(self) -> None:
        assert redact("account 123456789") == f"account {NUMBER_TOKEN}"

    @pytest.mark.parametrize(
        "text",
        ["₹5,000 in 2 instalments", "born in 2026", "12345678", "call 108 or 102", "1234 5678"],
    )
    def test_ordinary_numbers_untouched(self, text: str) -> None:
        assert redact(text) == text


class TestRateLimit:
    def test_sliding_window(self) -> None:
        now = [0.0]
        window = SlidingWindow(2, 60, lambda: now[0])
        assert window.retry_after("k") is None
        window.record("k")
        window.record("k")
        assert window.retry_after("k") == 60
        now[0] = 30
        assert window.retry_after("k") == 30
        now[0] = 60.5
        assert window.retry_after("k") is None
        assert window.retry_after("other") is None

    async def test_ip_session_and_daily_cap(self) -> None:
        now = [0.0]
        counts = {"s1": 0}

        async def daily(session_id: str, since: datetime) -> int:
            return counts.get(session_id, 0)

        limiter = RateLimiter(
            per_minute=2,
            daily_cap=5,
            daily_counter=daily,
            clock=lambda: now[0],
            wall_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        )
        limiter.record("1.1.1.1", "s1")
        limiter.record("1.1.1.1", "s2")
        with pytest.raises(ChatError) as info:
            limiter.check_ip("1.1.1.1")
        assert info.value.code is ErrorCode.RATE_LIMIT and info.value.retry_after == 60
        limiter.check_ip("2.2.2.2")
        await limiter.check_session("s1")  # one hit only
        limiter.record("3.3.3.3", "s1")
        with pytest.raises(ChatError):
            await limiter.check_session("s1")  # per-session minute limit across IPs
        now[0] = 61
        counts["s1"] = 5
        with pytest.raises(ChatError) as info:
            await limiter.check_session("s1")
        assert info.value.detail == "daily cap"


class TestCookies:
    def test_issue_and_verify(self) -> None:
        signer = CookieSigner("secret")
        client_id, value = signer.issue()
        assert signer.verify(value) == client_id
        assert CookieSigner("secret").verify(value) == client_id  # stable across restarts

    @pytest.mark.parametrize("value", [None, "", "abc", "a.b.c", "x.", ".sig"])
    def test_rejects_malformed(self, value: str | None) -> None:
        assert CookieSigner("secret").verify(value) is None

    def test_rejects_tampering_and_other_secret(self) -> None:
        signer = CookieSigner("secret")
        _, value = signer.issue()
        assert signer.verify("other" + value) is None
        assert CookieSigner("different").verify(value) is None

    def test_missing_secret_still_works_for_process(self) -> None:
        signer = CookieSigner(None)
        assert not signer.configured
        client_id, value = signer.issue()
        assert signer.verify(value) == client_id


@pytest.fixture
def restore_root_logging():  # type: ignore[no-untyped-def]
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers, root.level = handlers, level
    configure_logging_flag_off()


def configure_logging_flag_off() -> None:
    import app.logging_setup as setup

    setup._log_content = False


@pytest.mark.usefixtures("restore_root_logging")
class TestLogging:
    def test_json_lines_with_request_id_and_no_content(self) -> None:
        configure_logging("INFO", log_message_content=False)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        log = logging.getLogger("test.json")
        log.addHandler(handler)
        token = request_id_var.set("req-123")
        try:
            log.info("hello", extra={"question": log_content("my number 9876543210")})
        finally:
            request_id_var.reset(token)
            log.removeHandler(handler)
        line = json.loads(stream.getvalue())
        assert line["request_id"] == "req-123" and line["msg"] == "hello"
        assert line["question"] is None

    def test_content_logging_is_redacted_when_enabled(self) -> None:
        configure_logging("INFO", log_message_content=True)
        assert log_content("call 9876543210") == f"call {PHONE_TOKEN}"
