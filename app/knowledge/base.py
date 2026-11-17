"""Knowledge types and the Retriever protocol."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

DocStatus = Literal["sample", "verified", "draft"]
DocType = Literal["scheme", "contact", "process", "faq", "glossary"]
SAMPLE_MARKER = "[SAMPLE DATA]"


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    file: str  # path relative to the knowledge dir, POSIX style
    doc_id: str
    title: str
    heading: str
    language: str
    status: DocStatus
    doc_type: DocType
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    text: str  # sample chunks start with SAMPLE_MARKER
    order: int  # position of the chunk inside its file

    @property
    def is_sample(self) -> bool:
        return self.status == "sample"


@dataclass(frozen=True, slots=True)
class FileProblem:
    file: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True, slots=True)
class LoadedFile:
    file: str
    doc_id: str
    language: str
    status: DocStatus
    chunk_count: int


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    version: int
    chunks: tuple[KnowledgeChunk, ...]
    files: tuple[LoadedFile, ...]
    problems: tuple[FileProblem, ...]
    loaded_at: datetime
    fingerprint: tuple[tuple[str, int, int], ...] = field(default=())


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    text: str
    language: str
    recent_user_turns: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: KnowledgeChunk
    score: float


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: tuple[ScoredChunk, ...]
    estimated_tokens: int
    mode: Literal["all", "matched", "none"]


class Retriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...
