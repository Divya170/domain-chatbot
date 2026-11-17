# Final review

## Verification status (read this first)

| Area | Status in the build sandbox |
|---|---|
| Knowledge loader, index, retriever, prompt, history, errors, tokens, LLM adapters (fake + Gemini with a fake client), storage (SQLite + memory), redaction, rate limits, cookies, logging, chat service, middleware, SSE, in-process UI client, Markdown sanitiser, UI strings, scripts | **Tested here: 198 passed.** ruff clean; mypy strict clean on these modules. |
| `app/config.py` (pydantic-settings) | Written; its test skipped here (package not installed). |
| FastAPI routes and app factory (`app/main.py`, `app/container.py`, `app/api/{deps,chat,sessions,feedback,meta}.py`) | **Written but not run here.** `tests/test_api.py` (20 test cases) runs once FastAPI and httpx are installed. |
| NiceGUI page (`app/ui/page.py`, `app/ui/components.py`) | **Written but not run here.** `tests/test_ui.py` covers page load, config, welcome and starters. Rehydration is covered at API level plus a manual check (below). |
| Real Gemini calls, Docker build | Not possible here (no network, no API key). |

### First run on your machine

```bash
pip install -r requirements-dev.txt
pytest -q                       # expect 0 skipped once all packages are installed
mypy && ruff check .
uvicorn app.main:create_app --factory --port 8000
```

Then check these by hand in the browser (the UI behaviour isn't automated):
1. The welcome text and 4 starter chips appear, and switching language changes them.
2. Sending a message shows typing dots, then streaming text with a caret, then a
   "Sources" expander.
3. **Stop generating** halts the stream. Regenerate replaces the last answer.
4. Refresh the page and the conversation comes back.
5. "Delete conversation" clears it, and a refresh shows the welcome again.
6. Remove `GEMINI_API_KEY`: a translated "not set up" error appears and the typed text
   is restored into the composer.

If a NiceGUI call fails, the most likely spots are `ui.html(..., sanitize=False)` (there
is a fallback), `.on(..., js_handler=...)` and `ui.run_with(..., dark=None)`. All three are
in `app/ui/`.

## §3: old problems and where each is fixed

| # | Old problem | Fixed in |
|---|---|---|
| 1 | `json` not imported (knowledge sent as Python repr) | Firestore code removed; knowledge is rendered by `app/prompts/builder.py::format_chunk` |
| 2 | Database re-read on every message | `app/knowledge/index.py`: loaded once, re-checked by mtime/size at most every `KB_RELOAD_CHECK_SECONDS` |
| 3 | Limit compared against the wrong counter | Replaced by the token budget in `app/knowledge/keyword.py` and `app/prompts/builder.py` |
| 4 | English-only matching, arbitrary fallback | `app/knowledge/text.py` (Unicode tokens), aliases in front-matter, `keyword.py` (no-match → nothing). Tests: `tests/test_retrieval.py` (Tamil, Hindi) |
| 5 | No rate limiting | `app/security/rate_limit.py` (per IP, per session, daily cap), used in `chat_service.py` |
| 6 | No streaming, timeout or cancellation | `app/api/sse.py`, `app/llm/gemini.py` (per-chunk timeout), disconnect → provider closed (`tests/test_asgi_stack.py`) |
| 7 | Unpinned deps, Gunicorn not wired, localhost bind | `requirements*.txt`, `Dockerfile` (Gunicorn + Uvicorn worker on 0.0.0.0) |
| 8 | History trusted from the browser | `chat_service.py` loads history from storage; the request's `history` field is ignored |
| 9 | One generic error text | `app/services/errors.py` (8 typed codes) + `app/ui/strings.py` (translated per code) |

## §9: Hashbooks defects not repeated

| Defect | How it is avoided |
|---|---|
| Admin routes without an admin check | `app/api/deps.py::require_admin` on the whole admin router: 404 with no key configured, 401 with no header, 403 with a wrong key, constant-time compare |
| `tokensUsed` never assigned | Real `usage_metadata` → `messages.input_tokens/output_tokens` and the `done` frame |
| Dead "contact support" flag | No workflow fields without a writer |
| Raw error text to the client | `classify()` + `PUBLIC_MESSAGE`; details are only logged (`tests/test_core_misc.py`, `test_chat_service.py`) |
| Logging every question verbatim | `LOG_MESSAGE_CONTENT=false` by default; when on, text is redacted (`app/logging_setup.py`) |
| No timeout or cancellation | Per-chunk timeout, `aclosing` + disconnect handling, cancelled turns stored |
| Plain-text Markdown | `app/ui/markdown.py` (sanitised markdown-it) |
| History vanishes on refresh | `GET /api/sessions/{id}/messages` + `app.storage.user` session id |
| One generic error string | Typed codes, translated messages |
| Retrying 429 with tiny backoff | 429 is never retried; 5xx/network retried only before the first chunk, with a 1 s/2 s backoff |
| Uncaught async DB writes after the stream | Writes are awaited before `done`; cancelled writes are tracked tasks with logged failures, drained at shutdown |
| Comments contradicting code | `MAX_HISTORY_MESSAGES` documented and tested as rows, whole turns only |
| Env vars nothing reads | `tests/test_core_misc.py::test_env_example_matches_settings` |
| Half-connected abstractions | `ChatService` depends only on the `Retriever`, `LlmProvider` and `ChatRepository` protocols |
| Restart needed to update the KB | Hot reload with last-good protection (`tests/test_knowledge_index.py`) |

## Commands

```bash
uvicorn app.main:create_app --factory --reload            # dev
docker compose up --build -d                              # production-like
pytest --cov=app --cov-report=term-missing                # tests + coverage (target ≥ 85%)
python scripts/lint_kb.py && python scripts/check_kb.py "question" --language ta
```

## What you must provide

1. **`GEMINI_API_KEY`** and **`GEMINI_MODEL`** (a current stable id; there is no default in code).
2. **`SESSION_SECRET`** (a long random string) and optionally **`ADMIN_API_KEY`**.
3. **Real scheme content** for every file still marked `status: sample`:
   `schemes/pmmvy.md`, `schemes/jsy.md`, `schemes/jssk.md`, `schemes/pmsma.md`,
   `schemes/picme-rch.md`, `general/contacts.md`, `general/grievance.md`,
   `general/antenatal-care-basics.md`. Topics in `allowed_topics` with no file yet: Dr.
   Muthulakshmi Reddy scheme, LaQshya, SUMAN, Amma Baby Care Kit, 102 ambulance, ICDS.
4. **Official contacts** for `domain.py` (`TODO_CONTACTS`): maternal-health helpline,
   ambulance/transport number, emergency number, scheme portals, grievance channel.
5. **Native-speaker review** of all non-English text in `app/domain.py` and
   `app/ui/strings.py`, and of the Tamil aliases in the knowledge files.
6. **The final name, colours or logo, and the exact disclaimer wording.**
7. **The deployment target and public domain** (for `ALLOWED_ORIGINS`, if other origins
   call the API, and for TLS), plus confirmation that a single host with one volume is
   acceptable.

## Known limitations

* The UI page needs a weaker CSP (`'unsafe-inline' 'unsafe-eval'`), because NiceGUI
  requires it. The API routes get `default-src 'none'`.
* There is no offline mode beyond NiceGUI's reconnect overlay.
* Fonts use a Noto-first CSS stack that relies on fonts installed on the user's device
  (standard on Android). Self-hosting the font files is optional and not included.
* Retrieval is keyword-based. For paraphrases with no shared words or aliases, add
  aliases, or add a vector retriever behind `Retriever`.
