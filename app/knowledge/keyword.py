"""Default retriever: Unicode-aware keyword scoring with aliases and a token budget.

Scoring per chunk (current question; recent user turns count at half weight):
  * file alias / id / title match ............ +10   (applies to every chunk of the file)
  * section heading match ...................... +4
  * tag match .................................. +2 each, capped at +4
  * content term frequency ..................... +0.5 per hit (max 3 per term), capped at +3
  * chunk in the selected language ............. +0.5
Chunks below 50% of the best score are dropped. The file's Overview chunk is always
attached when another chunk of that file is selected. Selection stops at the token
budget. If the whole (language-resolved) knowledge base fits in the budget, it is
returned in full. Nothing matched and too large to send whole → no chunks.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.knowledge.base import (
    KnowledgeChunk,
    KnowledgeSnapshot,
    RetrievalQuery,
    RetrievalResult,
    ScoredChunk,
)
from app.knowledge.text import contains_phrase, normalise, significant_tokens, tokenise
from app.services.tokens import estimate_tokens

DOC_MATCH_SCORE = 10.0
HEADING_SCORE = 4.0
TAG_SCORE = 2.0
TAG_CAP = 4.0
CONTENT_HIT_SCORE = 0.5
CONTENT_TERM_CAP = 3
CONTENT_CAP = 3.0
LANGUAGE_BOOST = 0.5
RECENT_WEIGHT = 0.5
RELATIVE_THRESHOLD = 0.5
MIN_SCORE = 1.0
RECENT_TURNS = 3
PREFIX_MIN_LEN = 3
OVERVIEW = "Overview"
FALLBACK_LANGUAGE = "en"


@dataclass(frozen=True, slots=True)
class _Prepared:
    chunk: KnowledgeChunk
    tokens: Counter[str]
    heading_norm: str
    tag_tokens: frozenset[str]
    cost: int


@dataclass(frozen=True, slots=True)
class _Query:
    norm: str
    tokens: list[str]
    token_set: frozenset[str]


def _query(text: str) -> _Query:
    tokens = tokenise(text)
    return _Query(normalise(text), tokens, frozenset(significant_tokens(text)))


def chunk_cost(chunk: KnowledgeChunk) -> int:
    """Token cost of a chunk as it will appear in the prompt (with its header)."""
    return (
        estimate_tokens(chunk.text) + estimate_tokens(chunk.title + chunk.heading + chunk.file) + 12
    )


class KeywordRetriever:
    def __init__(
        self,
        snapshot_provider: Callable[[], KnowledgeSnapshot | None],
        *,
        max_context_tokens: int,
        heading_keywords: Mapping[str, Sequence[str]],
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self.max_context_tokens = max_context_tokens
        self._heading_keywords = {
            normalise(h): tuple(normalise(k) for k in kws) for h, kws in heading_keywords.items()
        }
        self._cache: tuple[int, list[_Prepared]] | None = None

    # -- preparation ---------------------------------------------------------------
    def _prepared(self, snapshot: KnowledgeSnapshot) -> list[_Prepared]:
        if self._cache is not None and self._cache[0] == snapshot.version:
            return self._cache[1]
        prepared = [
            _Prepared(
                chunk=c,
                tokens=Counter(significant_tokens(c.text)),
                heading_norm=normalise(c.heading),
                tag_tokens=frozenset(t for tag in c.tags for t in significant_tokens(tag)),
                cost=chunk_cost(c),
            )
            for c in snapshot.chunks
        ]
        self._cache = (snapshot.version, prepared)
        return prepared

    @staticmethod
    def _resolve_languages(items: list[_Prepared], language: str) -> list[_Prepared]:
        """Per document id keep one language: the selected one, else English, else any."""
        by_doc: dict[str, set[str]] = {}
        for p in items:
            by_doc.setdefault(p.chunk.doc_id, set()).add(p.chunk.language)
        chosen: dict[str, str] = {}
        for doc_id, langs in by_doc.items():
            if language in langs:
                chosen[doc_id] = language
            elif FALLBACK_LANGUAGE in langs:
                chosen[doc_id] = FALLBACK_LANGUAGE
            else:
                chosen[doc_id] = sorted(langs)[0]
        return [p for p in items if p.chunk.language == chosen[p.chunk.doc_id]]

    # -- scoring -------------------------------------------------------------------
    @staticmethod
    def _doc_match(chunk: KnowledgeChunk, q: _Query) -> bool:
        if contains_phrase(q.norm, q.tokens, chunk.doc_id):
            return True
        if any(contains_phrase(q.norm, q.tokens, alias) for alias in chunk.aliases):
            return True
        title_tokens = set(significant_tokens(chunk.title))
        hits = title_tokens & q.token_set
        return len(hits) >= 2 and len(hits) * 2 >= len(title_tokens)

    def _heading_match(self, p: _Prepared, q: _Query) -> bool:
        base = p.heading_norm.split(" › ")[0]
        keywords = self._heading_keywords.get(base, ())
        if any(contains_phrase(q.norm, q.tokens, k) for k in keywords):
            return True
        heading_tokens = set(significant_tokens(p.chunk.heading))
        return bool(heading_tokens & q.token_set)

    @staticmethod
    def _content_score(p: _Prepared, q: _Query) -> float:
        score = 0.0
        for term in q.token_set:
            count = p.tokens.get(term, 0)
            if not count and not term.isascii() and len(term) >= PREFIX_MIN_LEN:
                count = sum(
                    n
                    for tok, n in p.tokens.items()
                    if len(tok) >= PREFIX_MIN_LEN and (tok.startswith(term) or term.startswith(tok))
                )
            score += CONTENT_HIT_SCORE * min(count, CONTENT_TERM_CAP)
        return min(score, CONTENT_CAP)

    def _score(self, p: _Prepared, q: _Query, language: str) -> float:
        score = 0.0
        if self._doc_match(p.chunk, q):
            score += DOC_MATCH_SCORE
        if self._heading_match(p, q):
            score += HEADING_SCORE
        score += min(TAG_SCORE * len(p.tag_tokens & q.token_set), TAG_CAP)
        score += self._content_score(p, q)
        if score > 0 and p.chunk.language == language:
            score += LANGUAGE_BOOST
        return score

    # -- selection -----------------------------------------------------------------
    @staticmethod
    def _ordered(selected: list[ScoredChunk]) -> tuple[ScoredChunk, ...]:
        """Group by file (best file first), chunks in file order."""
        best: dict[str, float] = {}
        for s in selected:
            best[s.chunk.file] = max(best.get(s.chunk.file, 0.0), s.score)
        return tuple(
            sorted(selected, key=lambda s: (-best[s.chunk.file], s.chunk.file, s.chunk.order))
        )

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        snapshot = self._snapshot_provider()
        if snapshot is None or not snapshot.chunks:
            return RetrievalResult((), 0, "none")
        items = self._resolve_languages(self._prepared(snapshot), query.language)

        current = _query(query.text)
        recent_text = " ".join(list(query.recent_user_turns)[-RECENT_TURNS:])
        recent = _query(recent_text) if recent_text.strip() else None
        scored = []
        for p in items:
            score = self._score(p, current, query.language)
            if recent is not None:
                score += RECENT_WEIGHT * self._score(p, recent, query.language)
            scored.append((p, score))

        total_cost = sum(p.cost for p in items)
        if total_cost <= self.max_context_tokens:
            everything = [ScoredChunk(p.chunk, s) for p, s in scored]
            return RetrievalResult(self._ordered(everything), total_cost, "all")

        best = max((s for _, s in scored), default=0.0)
        if best < MIN_SCORE:
            return RetrievalResult((), 0, "none")
        threshold = max(MIN_SCORE, best * RELATIVE_THRESHOLD)
        candidates = sorted(((p, s) for p, s in scored if s >= threshold), key=lambda ps: -ps[1])
        overviews = {p.chunk.file: (p, s) for p, s in scored if p.chunk.heading == OVERVIEW}

        chosen: dict[tuple[str, int], ScoredChunk] = {}
        used = 0
        for p, s in candidates:
            if (p.chunk.file, p.chunk.order) in chosen:
                continue
            bundle = [(p, s)]
            overview = overviews.get(p.chunk.file)
            if (
                overview
                and (p.chunk.file, overview[0].chunk.order) not in chosen
                and overview[0] is not p
            ):
                bundle.insert(0, overview)
            cost = sum(b.cost for b, _ in bundle)
            if used + cost > self.max_context_tokens:
                continue
            for b, bs in bundle:
                chosen[(b.chunk.file, b.chunk.order)] = ScoredChunk(b.chunk, bs)
            used += cost
        if not chosen:
            return RetrievalResult((), 0, "none")
        return RetrievalResult(self._ordered(list(chosen.values())), used, "matched")
