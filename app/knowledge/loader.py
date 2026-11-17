"""Scan ``knowledge/``, validate front-matter and split bodies into chunks.

One bad file never breaks the knowledge base: it is skipped and reported as a
``FileProblem``. ``lint_document`` adds stricter content checks used by
``scripts/lint_kb.py`` (CI), which the running app reports but does not enforce.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.knowledge.base import (
    SAMPLE_MARKER,
    DocStatus,
    DocType,
    FileProblem,
    KnowledgeChunk,
    LoadedFile,
)
from app.services.tokens import estimate_tokens

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 512 * 1024
DEFAULT_MAX_CHUNK_TOKENS = 700
LANGUAGE_CODES = ("en", "ta")
STANDARD_SECTIONS = (
    "Overview",
    "Eligibility",
    "Benefits",
    "Documents required",
    "How to apply",
    "Status check",
    "Official contacts",
    "Notes",
)
INTRO_HEADING = "Introduction"

_H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_H3 = re.compile(r"^###\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)\+?\d[\d\s\-()]{6,}\d(?!\d)")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    title: str = Field(min_length=1)
    type: DocType
    language: str
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_url: str = ""
    last_verified: str = ""
    status: DocStatus

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError("id must be lowercase letters, digits, '-' or '_' with no spaces")
        return value

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        if value not in LANGUAGE_CODES:
            raise ValueError(f"language must be one of {', '.join(LANGUAGE_CODES)}")
        return value

    @field_validator("last_verified")
    @classmethod
    def _check_date(cls, value: str) -> str:
        if value:
            date.fromisoformat(value)
        return value

    @field_validator("aliases", "tags", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return [] if value is None else value


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    file: str
    meta: FrontMatter
    body: str
    chunks: tuple[KnowledgeChunk, ...]


@dataclass(frozen=True, slots=True)
class LoadResult:
    documents: tuple[ParsedDocument, ...]
    problems: tuple[FileProblem, ...]

    @property
    def chunks(self) -> tuple[KnowledgeChunk, ...]:
        return tuple(c for d in self.documents for c in d.chunks)

    @property
    def files(self) -> tuple[LoadedFile, ...]:
        return tuple(
            LoadedFile(d.file, d.meta.id, d.meta.language, d.meta.status, len(d.chunks))
            for d in self.documents
        )


class DocumentError(ValueError):
    """A file that cannot be indexed."""


def iter_markdown_files(root: Path) -> Iterator[Path]:
    """Yield indexable ``.md`` files under ``root`` in a stable order.

    Directory symlinks are not followed. Names starting with ``_`` (files or folders),
    hidden entries and ``README.md`` are skipped.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(("_", ".")))
        for name in sorted(filenames):
            if not name.endswith(".md") or name.startswith(("_", ".")):
                continue
            if name.lower() == "readme.md":
                continue
            yield Path(dirpath) / name


def safe_relative(root: Path, path: Path) -> str:
    """Return the POSIX path of ``path`` inside ``root``; reject anything that resolves
    outside it (for example a symlink pointing elsewhere)."""
    root_resolved = root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise DocumentError("file resolves outside the knowledge directory")
    return path.relative_to(root).as_posix()


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise DocumentError("missing front-matter: the file must start with '---'")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            break
    else:
        raise DocumentError("front-matter is not closed with '---'")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise DocumentError(f"front-matter is not valid YAML ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise DocumentError("front-matter must be a YAML mapping")
    return data, body


def _sections(body: str, pattern: re.Pattern[str]) -> list[tuple[str | None, str]]:
    """Split text at headings matching ``pattern`` (ignoring fenced code)."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    in_fence = False
    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else pattern.match(line)
        if match:
            sections.append((match.group(1).strip(), []))
        else:
            sections[-1][1].append(line)
    return [(heading, "\n".join(lines).strip()) for heading, lines in sections]


def _split_paragraphs(text: str, max_tokens: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if current and estimate_tokens(candidate) > max_tokens:
            parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def chunk_body(body: str, max_tokens: int) -> list[tuple[str, str]]:
    """Return (heading, text) pairs: one per H2 section, split further by H3 and then
    by paragraph when a section exceeds ``max_tokens``."""
    result: list[tuple[str, str]] = []
    for heading, text in _sections(body, _H2):
        if not text:
            continue
        name = heading or INTRO_HEADING
        if estimate_tokens(text) <= max_tokens:
            result.append((name, text))
            continue
        for sub_heading, sub_text in _sections(text, _H3):
            if not sub_text:
                continue
            sub_name = f"{name} › {sub_heading}" if sub_heading else name
            for piece in _split_paragraphs(sub_text, max_tokens):
                result.append((sub_name, piece))
    return result


def parse_document(
    root: Path, path: Path, *, max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS
) -> ParsedDocument:
    rel = safe_relative(root, path)
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise DocumentError(f"file is larger than {MAX_FILE_BYTES // 1024} KB")
    try:
        text = unicodedata.normalize("NFC", raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise DocumentError("file is not valid UTF-8") from exc
    data, body = split_front_matter(text)
    try:
        meta = FrontMatter.model_validate(data)
    except ValidationError as exc:
        fields = ", ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'front-matter'}: {err['msg']}"
            for err in exc.errors()
        )
        raise DocumentError(f"invalid front-matter ({fields})") from exc
    if not body.strip():
        raise DocumentError("body is empty")
    pieces = chunk_body(body, max_chunk_tokens)
    if not pieces:
        raise DocumentError("body has no content")
    chunks = tuple(
        KnowledgeChunk(
            file=rel,
            doc_id=meta.id,
            title=meta.title,
            heading=heading,
            language=meta.language,
            status=meta.status,
            doc_type=meta.type,
            aliases=tuple(unicodedata.normalize("NFC", a) for a in meta.aliases if a),
            tags=tuple(meta.tags),
            text=f"{SAMPLE_MARKER} {chunk_text}" if meta.status == "sample" else chunk_text,
            order=order,
        )
        for order, (heading, chunk_text) in enumerate(pieces)
    )
    return ParsedDocument(file=rel, meta=meta, body=body, chunks=chunks)


def load_knowledge(root: Path, *, max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS) -> LoadResult:
    """Load every indexable file. Raises ``FileNotFoundError`` if ``root`` is missing."""
    if not root.is_dir():
        raise FileNotFoundError(f"knowledge directory not found: {root}")
    documents: list[ParsedDocument] = []
    problems: list[FileProblem] = []
    seen: dict[tuple[str, str], str] = {}
    for path in iter_markdown_files(root):
        label = path.relative_to(root).as_posix()
        try:
            doc = parse_document(root, path, max_chunk_tokens=max_chunk_tokens)
        except (DocumentError, OSError) as exc:
            message = str(exc) if isinstance(exc, DocumentError) else "file could not be read"
            problems.append(FileProblem(label, message))
            logger.warning("knowledge file skipped", extra={"file": label, "reason": message})
            continue
        key = (doc.meta.id, doc.meta.language)
        if key in seen:
            message = (
                f"duplicate id '{doc.meta.id}' for language '{doc.meta.language}' "
                f"(also in {seen[key]})"
            )
            problems.append(FileProblem(label, message))
            logger.warning("knowledge file skipped", extra={"file": label, "reason": message})
            continue
        seen[key] = label
        documents.append(doc)
    return LoadResult(tuple(documents), tuple(problems))


def lint_document(doc: ParsedDocument) -> list[FileProblem]:
    """Content checks for CI. Errors here do not stop the app from loading the file."""
    problems: list[FileProblem] = []
    meta = doc.meta
    if meta.status == "verified":
        if not meta.source_url:
            problems.append(FileProblem(doc.file, "verified file has no source_url", "warning"))
        if not meta.last_verified:
            problems.append(FileProblem(doc.file, "verified file has no last_verified", "warning"))
    if meta.status == "sample":
        searchable = "\n".join([doc.body, meta.source_url, meta.title])
        if _URL.search(searchable):
            problems.append(FileProblem(doc.file, "sample file contains a URL"))
        if _PHONE.search(searchable):
            problems.append(FileProblem(doc.file, "sample file contains a phone-like number"))
        if meta.last_verified:
            problems.append(FileProblem(doc.file, "sample file must not set last_verified"))
    if meta.type == "scheme":
        for heading, _ in _sections(doc.body, _H2):
            if heading is not None and heading not in STANDARD_SECTIONS:
                problems.append(
                    FileProblem(doc.file, f"non-standard section '## {heading}'", "warning")
                )
    for alias in meta.aliases:
        if len(alias.strip()) < 3:
            problems.append(FileProblem(doc.file, f"alias '{alias}' is too short to match safely"))
    return problems
