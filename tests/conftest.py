"""Shared fixtures. Async tests use the AnyIO pytest plugin (ships with AnyIO)."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

# Frozen sample knowledge base: tests must not depend on production content.
KB_ROOT = Path(__file__).resolve().parent / "fixtures" / "knowledge"
# The real knowledge base shipped with the app (only linted, never asserted on).
REAL_KB_ROOT = Path(__file__).resolve().parents[1] / "knowledge"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_doc(
    doc_id: str,
    *,
    title: str | None = None,
    language: str = "en",
    status: str = "verified",
    doc_type: str = "scheme",
    aliases: list[str] | None = None,
    tags: list[str] | None = None,
    body: str | None = None,
    extra_front: str = "",
) -> str:
    alias_yaml = ", ".join(f'"{a}"' for a in (aliases or []))
    tag_yaml = ", ".join(tags or [])
    source = (
        'source_url: "https://example.org/x"\nlast_verified: "2026-01-01"'
        if status == "verified"
        else 'source_url: ""\nlast_verified: ""'
    )
    if body is None:
        body = textwrap.dedent(
            f"""\
            ## Overview
            {doc_id} overview text.

            ## Eligibility
            - {doc_id} eligibility condition.

            ## Documents required
            - {doc_id} document list.
            """
        )
    return (
        "---\n"
        f"id: {doc_id}\n"
        f'title: "{title or doc_id.upper() + " scheme"}"\n'
        f"type: {doc_type}\n"
        f"language: {language}\n"
        f"aliases: [{alias_yaml}]\n"
        f"tags: [{tag_yaml}]\n"
        f"{source}\n"
        f"status: {status}\n"
        f"{extra_front}"
        "---\n\n"
        f"{body}"
    )


WriteDoc = Callable[..., Path]


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge"
    root.mkdir()
    return root


@pytest.fixture
def write_doc(kb_dir: Path) -> WriteDoc:
    def _write(relpath: str, content: str) -> Path:
        path = kb_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write
