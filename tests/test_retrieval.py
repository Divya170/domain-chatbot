from __future__ import annotations

from pathlib import Path

import pytest

from app.domain import DOMAIN
from app.knowledge.base import RetrievalQuery, RetrievalResult
from app.knowledge.index import KnowledgeIndex
from app.knowledge.keyword import KeywordRetriever, chunk_cost
from app.knowledge.text import contains_phrase, normalise, tokenise
from tests.conftest import KB_ROOT, WriteDoc, make_doc


def retriever_for(root: Path, budget: int) -> KeywordRetriever:
    index = KnowledgeIndex(root, reload_check_seconds=0)
    return KeywordRetriever(
        index.snapshot, max_context_tokens=budget, heading_keywords=DOMAIN.heading_keywords
    )


def ask(
    r: KeywordRetriever, text: str, language: str = "en", recent: tuple[str, ...] = ()
) -> RetrievalResult:
    return r.retrieve(RetrievalQuery(text, language, recent))


def files(result: RetrievalResult) -> set[str]:
    return {s.chunk.file for s in result.chunks}


def sections(result: RetrievalResult) -> list[tuple[str, str]]:
    return [(s.chunk.doc_id, s.chunk.heading) for s in result.chunks]


SMALL_BUDGET = 400  # far below the repository KB size, forces matching


class TestTokeniser:
    def test_indic_words_stay_whole(self) -> None:
        assert tokenise("மாத்ரு வந்தனா प्रधानमंत्री, PMMVY!") == [
            "மாத்ரு",
            "வந்தனா",
            "प्रधानमंत्री",
            "pmmvy",
        ]

    def test_ascii_alias_needs_token_boundary(self) -> None:
        text = "tell me about jsyx"
        assert not contains_phrase(normalise(text), tokenise(text), "JSY")
        text = "tell me about JSY please"
        assert contains_phrase(normalise(text), tokenise(text), "jsy")

    def test_indic_alias_matches_inflected_form(self) -> None:
        text = "மாத்ரு வந்தனாவுக்கு யார் தகுதி?"
        assert contains_phrase(normalise(text), tokenise(text), "மாத்ரு வந்தனா")


class TestRepositoryKnowledge:
    @pytest.fixture
    def r(self) -> KeywordRetriever:
        return retriever_for(KB_ROOT, SMALL_BUDGET)

    def test_english_query(self, r: KeywordRetriever) -> None:
        result = ask(r, "What is the eligibility for PMMVY?")
        assert result.mode == "matched"
        assert files(result) == {"schemes/pmmvy.md"}
        assert result.chunks[0].chunk.heading == "Overview"

    def test_tamil_query_through_alias(self, r: KeywordRetriever) -> None:
        result = ask(r, "மாத்ரு வந்தனா திட்டத்திற்கு என்ன ஆவணங்கள் தேவை?", "ta")
        assert files(result) == {"schemes/pmmvy.md"}
        best = max(result.chunks, key=lambda s: s.score)
        assert best.chunk.heading in {"Documents required", "Overview"}
        assert ("pmmvy", "Documents required") in sections(result)

    def test_heading_boost_documents(self, r: KeywordRetriever) -> None:
        result = ask(r, "Which documents are needed for JSY?")
        scores = {s.chunk.heading: s.score for s in result.chunks}
        assert scores["Documents required"] == max(scores.values())

    def test_overview_attached_without_doc_match(self) -> None:
        r = retriever_for(KB_ROOT, 250)
        result = ask(r, "how do I raise a grievance complaint")
        assert "general/grievance.md" in files(result)
        assert ("grievance", "Overview") in sections(result)

    def test_token_budget_respected(self, r: KeywordRetriever) -> None:
        for question in ("PMMVY eligibility", "documents", "status check for PICME and JSY"):
            result = ask(r, question)
            assert result.estimated_tokens <= SMALL_BUDGET
            assert sum(chunk_cost(s.chunk) for s in result.chunks) == result.estimated_tokens

    def test_no_match_returns_nothing(self, r: KeywordRetriever) -> None:
        for question in ("who won the cricket match yesterday", "recommend a good laptop", "?"):
            result = ask(r, question)
            assert result.mode == "none"
            assert result.chunks == ()

    def test_follow_up_uses_recent_turns(self, r: KeywordRetriever) -> None:
        result = ask(r, "and the documents?", recent=("Tell me about PMMVY",))
        best = max(result.chunks, key=lambda s: s.score)
        assert best.chunk.doc_id == "pmmvy"
        assert best.chunk.heading == "Documents required"
        assert files(result) == {"schemes/pmmvy.md"}

    def test_whole_kb_sent_when_it_fits(self) -> None:
        r = retriever_for(KB_ROOT, 100_000)
        result = ask(r, "anything at all")
        assert result.mode == "all"
        assert len(files(result)) == 8


class TestLanguagePreference:
    def test_selected_language_file_preferred(self, kb_dir: Path, write_doc: WriteDoc) -> None:
        write_doc("x.md", make_doc("x", aliases=["XSCHEME"]))
        write_doc(
            "x.ta.md",
            make_doc("x", language="ta", aliases=["XSCHEME"], body="## Overview\nதமிழ் விளக்கம்.\n"),
        )
        write_doc("y.md", make_doc("y", aliases=["YSCHEME"]))
        r = retriever_for(kb_dir, 30)
        tamil = ask(r, "XSCHEME", "ta")
        assert {s.chunk.language for s in tamil.chunks} == {"ta"}
        other = ask(r, "XSCHEME", "xx")  # unsupported language → English file
        assert {s.chunk.language for s in other.chunks} == {"en"}

    def test_empty_or_missing_kb(self, tmp_path: Path) -> None:
        r = retriever_for(tmp_path / "missing", 100)
        assert ask(r, "PMMVY").mode == "none"


def test_retriever_cache_follows_snapshot_version(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("a.md", make_doc("a", aliases=["AAAA"]))
    index = KnowledgeIndex(kb_dir, reload_check_seconds=0)
    r = KeywordRetriever(
        index.snapshot, max_context_tokens=1000, heading_keywords=DOMAIN.heading_keywords
    )
    assert files(ask(r, "AAAA")) == {"a.md"}
    write_doc("b.md", make_doc("b", aliases=["BBBB"]))
    index.reload()
    assert "b.md" in files(ask(r, "BBBB"))
