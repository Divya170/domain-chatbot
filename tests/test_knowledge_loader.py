from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.knowledge.base import SAMPLE_MARKER
from app.knowledge.loader import (
    chunk_body,
    lint_document,
    load_knowledge,
    parse_document,
)
from tests.conftest import KB_ROOT, WriteDoc, make_doc


def test_valid_file_is_split_by_h2(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("schemes/abc.md", make_doc("abc", aliases=["ABC"], tags=["maternity"]))
    result = load_knowledge(kb_dir)
    assert result.problems == ()
    chunks = result.chunks
    assert [c.heading for c in chunks] == ["Overview", "Eligibility", "Documents required"]
    first = chunks[0]
    assert first.file == "schemes/abc.md"
    assert first.doc_id == "abc"
    assert first.aliases == ("ABC",)
    assert first.tags == ("maternity",)
    assert first.status == "verified"
    assert [c.order for c in chunks] == [0, 1, 2]
    assert "abc overview text." in first.text


def test_bad_front_matter_is_skipped_and_reported(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("good.md", make_doc("good"))
    write_doc("no_front.md", "## Overview\ntext\n")
    write_doc("bad_yaml.md", "---\nid: [unclosed\n---\n## Overview\nx\n")
    write_doc("unclosed.md", "---\nid: x\n")
    write_doc("bad_field.md", make_doc("Bad Id"))
    write_doc("unknown_key.md", make_doc("unk", extra_front="colour: blue\n"))
    write_doc("bad_lang.md", make_doc("lang", language="fr"))
    write_doc("bad_date.md", make_doc("dt", extra_front="").replace("2026-01-01", "01/01/2026"))
    result = load_knowledge(kb_dir)
    assert [d.meta.id for d in result.documents] == ["good"]
    reported = {p.file: p.message for p in result.problems}
    assert set(reported) == {
        "no_front.md",
        "bad_yaml.md",
        "unclosed.md",
        "bad_field.md",
        "unknown_key.md",
        "bad_lang.md",
        "bad_date.md",
    }
    assert "missing front-matter" in reported["no_front.md"]
    assert "not valid YAML" in reported["bad_yaml.md"]
    assert "id" in reported["bad_field.md"]
    assert "colour" in reported["unknown_key.md"]
    assert all(p.severity == "error" for p in result.problems)


def test_duplicate_id_same_language_skipped(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("a.md", make_doc("dup"))
    write_doc("b.md", make_doc("dup"))
    write_doc("b_ta.md", make_doc("dup", language="ta"))  # other language is allowed
    result = load_knowledge(kb_dir)
    assert [(d.file, d.meta.language) for d in result.documents] == [
        ("a.md", "en"),
        ("b_ta.md", "ta"),
    ]
    assert len(result.problems) == 1
    assert result.problems[0].file == "b.md"
    assert "duplicate id 'dup'" in result.problems[0].message


def test_empty_file_and_empty_body_skipped(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("empty.md", "")
    write_doc("nobody.md", make_doc("nobody", body="   \n\n"))
    result = load_knowledge(kb_dir)
    assert result.documents == ()
    assert {p.file for p in result.problems} == {"empty.md", "nobody.md"}


def test_underscore_readme_and_non_md_ignored(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("_TEMPLATE.md", make_doc("template"))
    write_doc("README.md", "# readme")
    write_doc("schemes/README.md", "# readme")
    write_doc("_drafts/x.md", make_doc("x"))
    write_doc(".hidden/y.md", make_doc("y"))
    write_doc("notes.txt", "not markdown")
    write_doc("real.md", make_doc("real"))
    result = load_knowledge(kb_dir)
    assert [d.file for d in result.documents] == ["real.md"]
    assert result.problems == ()


def test_symlink_escape_rejected(kb_dir: Path, tmp_path: Path, write_doc: WriteDoc) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(make_doc("secret"), encoding="utf-8")
    os.symlink(outside, kb_dir / "link.md")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "z.md").write_text(make_doc("z"), encoding="utf-8")
    os.symlink(outside_dir, kb_dir / "linked_dir")
    write_doc("inside.md", make_doc("inside"))
    result = load_knowledge(kb_dir)
    assert [d.meta.id for d in result.documents] == ["inside"]
    assert [(p.file, p.message) for p in result.problems] == [
        ("link.md", "file resolves outside the knowledge directory")
    ]


def test_symlink_inside_root_is_allowed(kb_dir: Path, write_doc: WriteDoc) -> None:
    target = write_doc("real.md", make_doc("real"))
    other = kb_dir / "sub"
    other.mkdir()
    os.symlink(target, other / "alias.md")
    result = load_knowledge(kb_dir)
    # Same id twice → the second is reported as a duplicate, but not as an escape.
    assert [p.message for p in result.problems] == [
        "duplicate id 'real' for language 'en' (also in real.md)"
    ]


def test_sample_chunks_carry_marker(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("s.md", make_doc("s", status="sample"))
    write_doc("v.md", make_doc("v"))
    chunks = {c.doc_id: c for c in load_knowledge(kb_dir).chunks if c.heading == "Overview"}
    assert chunks["s"].text.startswith(SAMPLE_MARKER)
    assert chunks["s"].is_sample
    assert not chunks["v"].text.startswith(SAMPLE_MARKER)


def test_large_section_split_by_h3_then_paragraph() -> None:
    long_para = "word " * 400
    body = f"## Benefits\n\n### Part one\n{long_para}\n\n{long_para}\n\n### Part two\nshort\n"
    pieces = chunk_body(body, max_tokens=300)
    headings = [h for h, _ in pieces]
    assert headings[0] == "Benefits › Part one"
    assert headings.count("Benefits › Part one") == 2
    assert headings[-1] == "Benefits › Part two"


def test_intro_text_and_fenced_headings() -> None:
    body = "Intro line.\n\n## Overview\n```\n## not a heading\n```\ntext\n"
    pieces = chunk_body(body, max_tokens=500)
    assert [h for h, _ in pieces] == ["Introduction", "Overview"]
    assert "## not a heading" in pieces[1][1]


def test_unicode_is_nfc_normalised(kb_dir: Path, write_doc: WriteDoc) -> None:
    decomposed = "café"
    write_doc("u.md", make_doc("u", aliases=[decomposed]))
    doc = load_knowledge(kb_dir).documents[0]
    assert doc.chunks[0].aliases == ("café",)


def test_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_knowledge(tmp_path / "nope")


def test_oversized_and_non_utf8_files(kb_dir: Path) -> None:
    (kb_dir / "big.md").write_bytes(b"x" * (600 * 1024))
    (kb_dir / "latin1.md").write_bytes("---\nid: l\n---\ncaf\xe9".encode("latin-1"))
    result = load_knowledge(kb_dir)
    messages = {p.file: p.message for p in result.problems}
    assert "larger than" in messages["big.md"]
    assert "UTF-8" in messages["latin1.md"]


class TestLint:
    def _doc(self, kb_dir: Path, write_doc: WriteDoc, content: str):  # type: ignore[no-untyped-def]
        path = write_doc("x.md", content)
        return parse_document(kb_dir, path)

    def test_verified_without_source_warns(self, kb_dir: Path, write_doc: WriteDoc) -> None:
        content = make_doc("x").replace("https://example.org/x", "").replace("2026-01-01", "")
        problems = lint_document(self._doc(kb_dir, write_doc, content))
        assert {(p.severity, p.message) for p in problems} == {
            ("warning", "verified file has no source_url"),
            ("warning", "verified file has no last_verified"),
        }

    def test_sample_with_url_or_phone_fails(self, kb_dir: Path, write_doc: WriteDoc) -> None:
        body = "## Overview\nVisit https://portal.example and call 98765 43210.\n"
        problems = lint_document(
            self._doc(kb_dir, write_doc, make_doc("x", status="sample", body=body))
        )
        messages = {p.message for p in problems if p.severity == "error"}
        assert messages == {
            "sample file contains a URL",
            "sample file contains a phone-like number",
        }

    def test_non_standard_section_and_short_alias(self, kb_dir: Path, write_doc: WriteDoc) -> None:
        body = "## Overview\nx\n## Random\ny\n"
        problems = lint_document(
            self._doc(kb_dir, write_doc, make_doc("x", aliases=["ab"], body=body))
        )
        assert {(p.severity, p.message) for p in problems} == {
            ("warning", "non-standard section '## Random'"),
            ("error", "alias 'ab' is too short to match safely"),
        }

    def test_repository_knowledge_base_is_clean(self) -> None:
        result = load_knowledge(KB_ROOT)
        assert result.problems == ()
        problems = [p for d in result.documents for p in lint_document(d)]
        assert [p for p in problems if p.severity == "error"] == []
        assert len([d for d in result.documents if d.meta.type == "scheme"]) >= 5
        assert len([d for d in result.documents if d.meta.type != "scheme"]) >= 3
        assert all(d.meta.status == "sample" for d in result.documents)
