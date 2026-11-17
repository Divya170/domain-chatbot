"""Validate every knowledge file. Exits 1 on any error (use in CI).

Usage: python scripts/lint_kb.py [--dir knowledge] [--strict]
  --strict  also fail on warnings
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge.base import FileProblem
from app.knowledge.loader import lint_document, load_knowledge


def run(root: Path, *, strict: bool = False) -> tuple[int, list[FileProblem], list[str]]:
    """Return (exit code, problems, sample files)."""
    try:
        result = load_knowledge(root)
    except FileNotFoundError as exc:
        return 1, [FileProblem(str(root), str(exc))], []
    problems = list(result.problems)
    for doc in result.documents:
        problems.extend(lint_document(doc))
    samples = [d.file for d in result.documents if d.meta.status == "sample"]
    errors = [p for p in problems if p.severity == "error"]
    warnings = [p for p in problems if p.severity == "warning"]
    failed = bool(errors) or (strict and bool(warnings))
    return (1 if failed else 0), problems, samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path("knowledge"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    code, problems, samples = run(args.dir, strict=args.strict)
    for p in sorted(problems, key=lambda p: (p.severity, p.file)):
        print(f"{p.severity.upper():7} {p.file}: {p.message}")
    if samples:
        print(f"\n{len(samples)} file(s) still contain SAMPLE data:")
        for s in samples:
            print(f"  - {s}")
    errors = sum(p.severity == "error" for p in problems)
    warnings = len(problems) - errors
    print(f"\n{errors} error(s), {warnings} warning(s).")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
