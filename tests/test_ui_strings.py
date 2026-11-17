"""UI text and Markdown safety (no NiceGUI needed)."""

from __future__ import annotations

import pytest

from app.domain import DOMAIN
from app.services.errors import ErrorCode
from app.ui.markdown import render_markdown
from app.ui.strings import ERRORS, KEYS, STRINGS, error_text, t


def test_every_ui_string_translated() -> None:
    for code in (lang.code for lang in DOMAIN.languages):
        assert set(STRINGS[code]) == set(KEYS), code
        assert set(ERRORS[code]) == set(ErrorCode), code
        assert len(set(ERRORS[code].values())) == len(ErrorCode), "messages must be distinct"


def test_translation_helpers() -> None:
    assert t("ta", "rate_limited", seconds=7).count("7") == 1
    assert t("xx", "send") == "Send"
    assert error_text("ta", "TIMEOUT") == ERRORS["ta"][ErrorCode.TIMEOUT]
    assert error_text("en", "UNKNOWN_CODE") == ERRORS["en"][ErrorCode.INTERNAL]


@pytest.mark.parametrize(
    ("source", "forbidden"),
    [
        ("<script>alert(1)</script>", "<script"),
        ("<img src=x onerror=alert(1)>", "<img"),
        ("[x](javascript:alert(1))", 'href="javascript'),
        ("[x](data:text/html;base64,PHNjcmlwdD4=)", 'href="data:'),
        ("![i](https://example.org/x.png)", "<img"),
    ],
)
def test_markdown_is_sanitised(source: str, forbidden: str) -> None:
    assert forbidden not in render_markdown(source)


def test_markdown_renders_lists_links_tables() -> None:
    html = render_markdown(
        "**b**\n\n1. one\n2. two\n\n[ok](https://example.org)\n\n|a|b|\n|-|-|\n|1|2|"
    )
    assert "<strong>b</strong>" in html and "<ol>" in html and "<table>" in html
    assert 'rel="noopener noreferrer nofollow"' in html and 'target="_blank"' in html
