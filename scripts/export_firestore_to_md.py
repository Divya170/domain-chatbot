"""ONE-OFF migration helper: export an old Firestore database to knowledge Markdown files.

This script is NOT part of the app and its dependency is NOT in requirements.txt.

    pip install firebase-admin
    python scripts/export_firestore_to_md.py --key path/to/service-account.json \\
        --out knowledge/imported

Every document becomes one file with ``status: draft`` (best effort). Fields whose
names look like a standard section (eligibility, benefits, documents, …) become that
``##`` section; everything else goes under ``## Notes``. A report lists the fields it
could not map. A person must review every generated file, move it into ``schemes/`` or
``general/``, check facts against the official source, then set ``status: verified``,
``source_url`` and ``last_verified``. Run ``python scripts/lint_kb.py`` afterwards.
Keep the service-account key out of the repository.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SECTION_HINTS: dict[str, tuple[str, ...]] = {
    "Overview": ("overview", "desc", "description", "about", "summary", "intro"),
    "Eligibility": ("elig", "eligibility", "criteria", "who"),
    "Benefits": ("ben", "benefit", "benefits", "amt", "amount", "assistance"),
    "Documents required": ("doc", "docs", "document", "documents", "proof"),
    "How to apply": ("apply", "application", "reg", "registration", "steps", "process"),
    "Status check": ("status", "track", "st"),
    "Official contacts": ("contact", "contacts", "helpline", "phone", "mob", "office", "dept"),
}
TITLE_KEYS = ("title", "name", "scheme", "scheme_name", "nm")


def slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[:60] or "doc"


def section_for(field: str) -> str | None:
    key = re.sub(r"[^a-z]", "", field.lower())
    for section, hints in SECTION_HINTS.items():
        if key in hints or any(key.startswith(h) and len(h) > 3 for h in hints):
            return section
    return None


def as_bullets(value: Any) -> list[str]:
    if isinstance(value, list):
        return [b for item in value for b in as_bullets(item)]
    if isinstance(value, dict):
        return [f"{k}: {json.dumps(v, ensure_ascii=False, default=str)}" for k, v in value.items()]
    text = str(value).strip()
    return [text] if text else []


def render(collection: str, doc_id: str, data: dict[str, Any]) -> tuple[str, str, list[str]]:
    title_key = next((k for k in TITLE_KEYS if isinstance(data.get(k), str)), None)
    title = str(data[title_key]) if title_key else f"{collection} {doc_id}"
    sections: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for field, value in data.items():
        if field == title_key:
            continue
        section = section_for(field)
        if section is None:
            unmapped.append(field)
            sections.setdefault("Notes", []).extend(
                f"(unconfirmed, field '{field}') {b}" for b in as_bullets(value)
            )
        else:
            sections.setdefault(section, []).extend(as_bullets(value))
    file_id = slug(f"{collection}-{doc_id}")
    front = (
        "---\n"
        f"id: {file_id}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        "type: scheme\n"
        "language: en\n"
        "aliases: []\n"
        f"tags: [{slug(collection)}]\n"
        'source_url: ""\n'
        'last_verified: ""\n'
        "status: draft\n"
        "---\n"
    )
    order = [*SECTION_HINTS, "Notes"]
    body = "\n".join(
        f"\n## {name}\n" + "\n".join(f"- {line}" for line in sections[name])
        for name in order
        if sections.get(name)
    )
    return file_id, front + body + "\n", unmapped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Firestore to knowledge Markdown")
    parser.add_argument("--key", type=Path, required=True, help="service-account JSON path")
    parser.add_argument("--out", type=Path, default=Path("knowledge/imported"))
    args = parser.parse_args(argv)
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        print("Install the helper dependency first: pip install firebase-admin")
        return 2

    firebase_admin.initialize_app(credentials.Certificate(str(args.key)))
    db = firestore.client()
    args.out.mkdir(parents=True, exist_ok=True)
    written = 0
    report: dict[str, list[str]] = {}
    for collection in db.collections():
        for snapshot in collection.stream():
            data = snapshot.to_dict() or {}
            file_id, text, unmapped = render(collection.id, snapshot.id, data)
            (args.out / f"{file_id}.md").write_text(text, encoding="utf-8")
            written += 1
            if unmapped:
                report[f"{file_id}.md"] = unmapped
    print(f"Wrote {written} draft file(s) to {args.out}")
    if report:
        print("\nFields that could not be mapped to a section (placed under ## Notes):")
        for file, fields in sorted(report.items()):
            print(f"  {file}: {', '.join(fields)}")
    print("\nNext: review every file by hand, then run python scripts/lint_kb.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
