# Knowledge base

The assistant answers from the Markdown files in this folder. This README and any file
whose name starts with `_` are **not** indexed.

## Add or edit a file

1. Copy `_TEMPLATE.md` into `schemes/` (or `general/`) and give it a clear name.
2. Fill in the front-matter. `id` must be unique per language.
3. Use the standard `##` sections: `Overview`, `Eligibility`, `Benefits`,
   `Documents required`, `How to apply`, `Status check`, `Official contacts`, `Notes`.
   Leave out sections you cannot verify. A missing section makes the assistant say
   the detail is not available.
4. Write facts explicitly, one per bullet, with units and conditions. No abbreviations
   like `elig` or `amt`.
5. Add `aliases` in every language people use (English and Tamil names). This is
   how a question in Tamil finds an English file.
6. Run `python scripts/lint_kb.py` and fix every error.
7. Try `python scripts/check_kb.py "your question" --language ta` to see what the
   assistant will receive.

The running app picks up changes within `KB_RELOAD_CHECK_SECONDS` (default 5 s), with
no restart needed. If an edit breaks a file, the app keeps serving the last good version and
lists the problem at `GET /api/admin/kb/status`.

## Status values

| status | meaning |
|---|---|
| `sample` | **Fake placeholder content** for development. Shown to the model as `[SAMPLE DATA]`; the assistant must not present it as fact. Must not contain URLs or phone numbers. |
| `draft` | Real content not yet checked against the official source. |
| `verified` | Checked by a person. Requires `source_url` and `last_verified`. |

## Language versions

A file in another language may reuse the same `id` (for example `pmmvy.ta.md` with
`language: ta`). When the user selects Tamil, the Tamil file is used; otherwise the
English file is used and the assistant translates.

## Files that still contain sample data

Every file under `schemes/` and `general/` is currently `status: sample` and must be
replaced with real, verified content before going live.
