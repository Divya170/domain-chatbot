# Building a different chatbot from this project

The engine (API, retrieval, storage, UI) is domain-neutral. To change the subject:

1. **`app/domain.py`**
   - Set `title`, `subtitle`, `domain`, `purpose`, `allowed_topics`,
     `out_of_domain_topics` and `refusal_scope` (the phrase used in "I can only help with …").
   - Edit `LANGUAGES` if you need a different set. Every text table must have an entry
     for each language code; `_texts()` raises an error if one is missing.
   - Rewrite `welcome`, `starters` (4 per language), `disclaimer`, `emergency` and
     `fallback` in every language, and have native speakers review them.
   - Fill `official_contacts` only with verified values. Leave it empty otherwise.
   - Adjust `heading_keywords` if your knowledge files use different `##` sections.
2. **Prompt.** Copy `app/prompts/system.v1.md` to `system.v2.md`, change the wording,
   set `PROMPT_VERSION = "system.v2"` in `app/prompts/builder.py`, and regenerate the
   snapshot (`UPDATE_SNAPSHOTS=1 pytest tests/test_prompt.py`). Keep every safety rule:
   knowledge-first, no invention, untrusted data, no sensitive-data requests, no
   diagnosis or advice beyond scope, no impersonation, never reveal secrets.
   `tests/test_prompt.py::SAFETY_PHRASES` checks them.
3. **Knowledge.** Delete the sample files, copy `knowledge/_TEMPLATE.md` for each topic,
   and add `aliases` in every language your users write in. If the new domain uses other
   standard sections, update `STANDARD_SECTIONS` in `app/knowledge/loader.py`. Run
   `python scripts/lint_kb.py`.
4. **Retrieval check.** Run `python scripts/check_kb.py "<typical question>" --language <code>`
   for each language and confirm that the right file and section come back.
5. **UI text.** Labels in `app/ui/strings.py` are generic. Review them, and add a
   language there if you added one in `domain.py`.
6. **Branding.** Colours and fonts are in `HEAD_HTML` in `app/ui/components.py`.
7. **Tests.** `tests/test_retrieval.py` uses the sample files (PMMVY, JSY, …). Point those
   tests at your new files and aliases.
