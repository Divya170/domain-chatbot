"""Errors, tokens, domain, scripts, config and the no-Firebase guarantee."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
from pathlib import Path

import pytest

from app.domain import DOMAIN, LANGUAGES
from app.services.errors import (
    HTTP_STATUS,
    PUBLIC_MESSAGE,
    ChatError,
    ErrorCode,
    classify,
    to_http,
    to_sse_frame,
)
from app.services.tokens import estimate_message_tokens, estimate_tokens
from scripts import check_kb, lint_kb
from tests.conftest import KB_ROOT, REAL_KB_ROOT, WriteDoc, make_doc

REPO = Path(__file__).resolve().parents[1]


class TestErrors:
    def test_every_code_has_status_and_message(self) -> None:
        assert set(HTTP_STATUS) == set(ErrorCode) == set(PUBLIC_MESSAGE)

    def test_http_mapping_hides_detail(self) -> None:
        status, body, headers = to_http(
            ChatError(ErrorCode.RATE_LIMIT, detail="ip=1.2.3.4", retry_after=7)
        )
        assert status == 429
        assert headers == {"Retry-After": "7"}
        assert "1.2.3.4" not in str(body)
        assert body["error"]["code"] == "RATE_LIMIT"

    def test_sse_frame(self) -> None:
        frame = to_sse_frame(ChatError(ErrorCode.TIMEOUT, detail="secret stack"))
        assert frame == {
            "type": "error",
            "code": "TIMEOUT",
            "message": PUBLIC_MESSAGE[ErrorCode.TIMEOUT],
        }

    def test_classify(self) -> None:
        assert classify(TimeoutError()).code is ErrorCode.TIMEOUT
        err = classify(KeyError("api_key=abc"))
        assert err.code is ErrorCode.INTERNAL and "abc" not in err.public_message
        original = ChatError(ErrorCode.KB_UNAVAILABLE)
        assert classify(original) is original


class TestTokens:
    def test_empty_and_latin(self) -> None:
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcd" * 10) == 10

    def test_indic_counts_more_per_char(self) -> None:
        assert estimate_tokens("மாத்ரு") > estimate_tokens("mathru")
        assert estimate_message_tokens("abcd") == 1 + 4


class TestDomain:
    def test_two_languages_with_exact_labels(self) -> None:
        assert [lang.label for lang in LANGUAGES] == [
            "English",
            "தமிழ் (Tamil)",
        ]

    @pytest.mark.parametrize(
        ("value", "code"),
        [
            ("ta", "ta"),
            ("தமிழ் (Tamil)", "ta"),
            ("hindi", "en"),
            ("  TA ", "ta"),
            ("fr", "en"),
            ("", "en"),
            (None, "en"),
        ],
    )
    def test_language_resolution(self, value: str | None, code: str) -> None:
        assert DOMAIN.language(value).code == code

    def test_every_text_translated(self) -> None:
        codes = {lang.code for lang in LANGUAGES}
        for table in (
            DOMAIN.welcome,
            DOMAIN.disclaimer,
            DOMAIN.emergency,
            DOMAIN.fallback,
            DOMAIN.starters,
        ):
            assert set(table) == codes
        assert all(len(v) == 4 for v in DOMAIN.starters.values())

    def test_no_invented_contacts(self) -> None:
        assert DOMAIN.official_contacts == ()
        phone = re.compile(r"\d{3,}")
        for table in (DOMAIN.welcome, DOMAIN.disclaimer, DOMAIN.emergency, DOMAIN.fallback):
            for text in table.values():
                assert not phone.search(text)
                assert "http" not in text

    def test_text_falls_back_to_default(self) -> None:
        assert DOMAIN.text(DOMAIN.welcome, "xx") == DOMAIN.welcome["en"]


class TestScripts:
    def test_lint_repository_passes(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert lint_kb.main(["--dir", str(KB_ROOT)]) == 0
        out = capsys.readouterr().out
        assert "8 file(s) still contain SAMPLE data" in out

    def test_real_knowledge_base_lints_clean(self) -> None:
        """The knowledge base that ships with the app must have no errors or warnings."""
        assert lint_kb.main(["--dir", str(REAL_KB_ROOT), "--strict"]) == 0

    def test_lint_fails_on_errors_and_strict_warnings(
        self, kb_dir: Path, write_doc: WriteDoc
    ) -> None:
        write_doc("bad.md", "no front matter")
        assert lint_kb.run(kb_dir)[0] == 1
        (kb_dir / "bad.md").unlink()
        write_doc("w.md", make_doc("w").replace("https://example.org/x", ""))
        assert lint_kb.run(kb_dir)[0] == 0
        assert lint_kb.run(kb_dir, strict=True)[0] == 1
        assert lint_kb.run(kb_dir / "missing")[0] == 1

    def test_check_kb_prints_matches(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert check_kb.main(["PMMVY documents", "--dir", str(KB_ROOT), "--budget", "400"]) == 0
        out = capsys.readouterr().out
        assert "mode=matched" in out and "schemes/pmmvy.md" in out
        assert check_kb.main(["x", "--dir", str(KB_ROOT / "missing")]) == 1


def test_firebase_is_not_installed_or_imported() -> None:
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.distribution("firebase-admin")
    assert importlib.util.find_spec("firebase_admin") is None
    for path in (REPO / "app").rglob("*.py"):
        assert "firebase" not in path.read_text(encoding="utf-8").lower(), path
    requirements = REPO / "requirements.txt"
    if requirements.exists():
        assert "firebase" not in requirements.read_text().lower()


def test_config_settings() -> None:
    pytest.importorskip("pydantic_settings")
    from app.config import Settings

    settings = Settings(
        _env_file=None, gemini_api_key="  ", allowed_origins="https://a.org/, https://b.org"
    )  # type: ignore[call-arg]
    assert settings.gemini_api_key is None
    assert not settings.gemini_configured
    assert settings.allowed_origin_list == ["https://a.org", "https://b.org"]
    assert settings.max_history_messages == 12 and settings.max_message_length == 4000


def test_env_example_matches_settings() -> None:
    env_vars = set(re.findall(r"^([A-Z_]+)=", (REPO / ".env.example").read_text(), re.MULTILINE))
    source = (REPO / "app" / "config.py").read_text()
    fields = {m.upper() for m in re.findall(r"^    ([a-z_]+): ", source, re.MULTILINE)}
    assert env_vars == fields


def test_firestore_export_render_maps_compact_fields() -> None:
    from scripts.export_firestore_to_md import render

    file_id, text, unmapped = render(
        "schemes", "abc", {"nm": "Test Scheme", "elig": ["cond A"], "amt": "X", "zz": 1}
    )
    assert file_id == "schemes-abc"
    assert "status: draft" in text and 'title: "Test Scheme"' in text
    assert "## Eligibility\n- cond A" in text and "## Benefits\n- X" in text
    assert unmapped == ["zz"] and "(unconfirmed, field 'zz') 1" in text
