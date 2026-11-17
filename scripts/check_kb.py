"""Show what the retriever would send to the model for a question.

Usage: python scripts/check_kb.py "question" [--language ta] [--dir knowledge]
       [--budget 6000] [--recent "earlier question" ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain import DOMAIN
from app.knowledge.base import RetrievalQuery
from app.knowledge.index import KnowledgeIndex
from app.knowledge.keyword import KeywordRetriever


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--language", default=DOMAIN.default_language)
    parser.add_argument("--dir", type=Path, default=Path("knowledge"))
    parser.add_argument("--budget", type=int, default=6000)
    parser.add_argument("--recent", nargs="*", default=[])
    args = parser.parse_args(argv)

    index = KnowledgeIndex(args.dir, reload_check_seconds=0)
    snapshot = index.reload()
    if snapshot is None:
        print(f"Could not load knowledge from {args.dir}")
        return 1
    retriever = KeywordRetriever(
        index.snapshot, max_context_tokens=args.budget, heading_keywords=DOMAIN.heading_keywords
    )
    language = DOMAIN.language(args.language).code
    result = retriever.retrieve(RetrievalQuery(args.question, language, tuple(args.recent)))
    print(
        f"mode={result.mode} chunks={len(result.chunks)} "
        f"estimated_tokens={result.estimated_tokens} budget={args.budget} language={language}"
    )
    for s in result.chunks:
        c = s.chunk
        print(f"  {s.score:5.1f}  {c.file}  §{c.heading}  [{c.status}/{c.language}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
