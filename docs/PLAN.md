# PLAN — Domain Chatbot on FastAPI (Markdown knowledge + SQLite)

Phase 1 output for `reference/FASTAPI_CHATBOT_MASTER_PROMPT.md`. No application code yet.
Section references like "§6.7" point to the master prompt.

---

## 1. What I read

| Source | Status |
|---|---|
| `reference/domain_chatbot.zip` → `app.py`, `chatbot_config.py`, `firebase_config.py`, `templates/index.html`, `requirements.txt`, `.gitignore`, `firebase-key.json` | Read in full. |
| `reference/chatbot-architecture-analysis.md` (Hashbooks) | **Not supplied.** §9 already lists the defects to avoid, so §9 is the checklist. If you send the file, I'll check it against this plan before Phase 3. |
| `firebase-key.json` | Placeholder only (`{"PLACEHOLDER": "Replace this…"}`), so no credential was leaked. It is deleted in the new project anyway. |

---

## 2. Findings in the existing code

### 2.1 The §3 problems, checked against the source

| §3 | Confirmed? | Detail |
|---|---|---|
| 1. `json` not imported | **Yes, but the effect is different.** | `json_safe()` (app.py:116) has its own `try/except Exception`, so the `NameError` is caught and it returns `str(value)`. Requests did **not** fail. Every document was sent to Gemini as a Python `repr` (single quotes, `None`, `datetime(...)`) instead of JSON. The fix is the same (the function is removed), but the effect was degraded context, not total failure. |
| 2. DB re-read on every message | Yes | `db.collections()` + `.stream()` runs on every `/api/chat`. |
| 3. Limit vs wrong counter | Yes | `if len(chunks) >= max_collections` compares the **chunk** count with the **collection** limit (app.py:79). |
| 4. English-only matching, arbitrary fallback | Yes, with one correction | `str.split()` is Unicode-aware, so Tamil tokens are produced. They still match nothing because the data is English. The fix is cross-language **aliases** (§6.4), not only tokenisation. The fallback `scanned <= 30` (app.py:95) sends the first 30 documents whatever they contain. |
| 5. No rate limiting | Yes | |
| 6. No streaming / timeout / cancel | Yes | Synchronous `generate_content` with no timeout blocks a worker thread. |
| 7. Unpinned deps, Gunicorn unused, localhost bind | Yes | `>=` pins only; `app.run(host="127.0.0.1")`. |
| 8. History trusted from browser | Yes | `payload["history"]` is used as-is (bounded and normalised, but not verified). |
| 9. One generic error text | Yes | Every failure returns the same "Sorry, the service could not complete your request…". |

### 2.2 Other problems found (also fixed)

1. **Substring matching gives false positives.** `token in searchable` matches "age" inside "page" and "reg" inside "pregnancy". The new retriever matches whole tokens.
2. **Hard-coded default model `gemini-2.5-flash`.** Google's model pages list Gemini 3.x as current, and Vertex AI lists the Gemini 2.5 models with retirement dates around June 2026, so the old default may not work at all now. (See §6 decision D4.)
3. **Language is sent only in the user turn**, not in the system instruction. The new prompt states it in both.
4. **The UI loses the typed message on error.** The input is cleared before the request is sent and never restored.
5. **The UI renders replies as plain text** (`textContent` + `pre-wrap`), so Markdown from the model shows as raw `**` and `-`.
6. **UI text is hard-coded in English:** subtitle, notice, welcome text and the four starter questions. The footnote mentions Firestore.
7. **The prompt mentions "Firebase credentials"** and the "compact key" rule (`ben`, `elig`, `amt`). Both go away with Markdown knowledge.
8. **The client-side history cap (12 messages) duplicates the server's cap.** Now there is one server-side limit only.

---

## 3. Old file → new location

| Old | New | Notes |
|---|---|---|
| `app.py` → routes | `app/main.py` (factory, lifespan, middleware), `app/api/chat.py`, `app/api/meta.py` | `POST /api/chat` is kept and still returns `reply` (plus new fields). |
| `app.py` → `_normalise_history` | `app/services/history.py` | History now comes from storage, not the browser. |
| `app.py` → `_retrieve_firestore_knowledge`, `json_safe` | `app/knowledge/loader.py`, `index.py`, `keyword.py` | Removed. Replaced by the local Markdown index. |
| `app.py` → Gemini call | `app/llm/gemini.py` behind `app/llm/base.py` | Streaming, timeout, retry, usage. |
| `app.py` → 413 handler | `app/main.py` body-limit middleware + `app/services/errors.py` | |
| `chatbot_config.py` → identity, topics, languages, limits | `app/domain.py` (domain values) + `app/config.py` (env limits) | |
| `chatbot_config.py` → `SYSTEM_PROMPT` | `app/prompts/system.v1.md` (template) + `app/prompts/builder.py` | "DATABASE-FIRST" becomes "KNOWLEDGE-FIRST RULES". The compact-key rule is dropped (§6.4). All safety rules are kept. |
| `firebase_config.py` | **Removed.** Replaced by `app/knowledge/loader.py` and `app/storage/sqlite_repo.py`. | |
| `firebase-key.json` | **Removed.** | `scripts/export_firestore_to_md.py` takes a key path as an argument instead. |
| `templates/index.html` | `app/ui/page.py`, `components.py`, `client.py` (NiceGUI) | The visual style (green palette, rounded shell) is carried over. |
| `requirements.txt` | `requirements.txt` (exact pins), `requirements-dev.txt` | `firebase-admin` removed. |
| `.gitignore` | `.gitignore` | Adds `data/`, `.nicegui/`, coverage files. |

---

## 4. Keep / change / add

**Keep:** same-origin chat page at `/`; `POST /api/chat` shape (`{message, language}` → `{reply}`); 256 KB body limit; 4000-character message limit; 12-message history cap; graceful 503 / `NOT_CONFIGURED` without a key; the 8 language labels exactly as written; every safety rule in the old prompt; temperature 0.25 and 1200 max output tokens as defaults.

**Change:** knowledge source (Markdown); retrieval (local index, aliases, token budget, no arbitrary fallback); history source (SQLite); single generic error → typed errors; plain text → sanitised Markdown; English-only UI text → 8 languages.

**Add:** SSE streaming, regenerate, feedback, session rehydration and delete, admin KB reload/status, rate limits, redaction, structured logging, security headers, hot reload of knowledge, `lint_kb.py` / `check_kb.py`, Docker, tests.

---

## 5. Stack confirmation (§4)

Kept as specified: Python 3.12, FastAPI, Uvicorn/Gunicorn, Pydantic v2, pydantic-settings, google-genai, python-frontmatter, markdown-it-py, stdlib `sqlite3`, NiceGUI, pytest + pytest-asyncio + httpx, ruff, mypy (strict on `app/`).

- **Pins:** exact versions are chosen and verified at the start of Phase 2, when the packages are installed. NiceGUI 4.0.0 has just been released; I'll pin 4.0.x if `ui.run_with` and the APIs used here behave as documented, otherwise the last 3.x release. Either way the choice is recorded in this file.
- **One-LLM-call rule:** kept. Retrieval and token estimation are local; there is no `count_tokens` call.

---

## 6. Decisions and assumptions

These are the points where the master prompt is ambiguous or where NiceGUI forces a choice. None is a blocker; each is reversible.

**D1. Session ownership uses a client id, not the session id, in the cookie.**
§6.7 asks for the session id in a signed HttpOnly cookie. With NiceGUI, "New chat" happens over a websocket after page load, and an HttpOnly cookie can only be set on an HTTP response. So:
- Middleware issues a signed, HttpOnly, `SameSite=Lax` **client id** cookie on the first HTTP request (the page load).
- `sessions` gets one extra column, `client_id TEXT NOT NULL` (indexed). Every session read, delete, regenerate and feedback call checks `session.client_id == cookie client id`. A mismatch returns 404 (no IDOR, no existence leak).
- New chat creates a new session for the same client id. No cookie change is needed.
- This is the only deviation from the §6.7 schema.

**D2. The UI calls the public API in-process, through a streaming ASGI transport.**
NiceGUI handlers run on the server, so "consume the same public API" means making HTTP calls from Python. httpx's built-in `ASGITransport` buffers the whole response, which breaks streaming. A loopback call to `127.0.0.1` would make every user look like the same IP to the rate limiter. So `app/ui/client.py` uses a small streaming ASGI transport that:
- goes through the full middleware stack and routing (same rate limits, validation, errors as any client);
- sets the ASGI `client` to the browser's real IP (taken after Uvicorn's proxy-header handling) and forwards the client-id cookie;
- delivers `http.disconnect` when the UI cancels, so **Stop generating** really aborts the Gemini call.

**D3. Languages use codes in the API.** `en ta hi te kn ml bn mr`. The old labels ("தமிழ் (Tamil)") are also accepted. An unknown value falls back to `en` (§10 says "unknown language → default", not an error).

**D4. No model id in code.** `GEMINI_MODEL` has no code default. If it's missing, chat returns `NOT_CONFIGURED` and `/health` shows it. `.env.example` suggests the current stable Flash id, verified when Phase 2 starts. At startup the adapter checks the id once (`models.get`) and logs the result.

**D5. Token estimates are heuristic** (no tokenizer offline, and no extra API call). The estimate is per script: roughly 4 characters per token for Latin text and a more conservative ratio for Indic scripts, which tokenise less efficiently. It is calibrated against `usage_metadata` from real calls in Phase 5. Budgets are therefore approximate but conservative.

**D6. Rate limits.** Per-IP and per-session per-minute windows are in memory. The **daily per-session cap is counted from SQLite**, so it survives restarts. Both return `RATE_LIMIT` with `Retry-After`.

**D7. One worker by default.** NiceGUI keeps each browser's UI state in process memory, and the per-minute limiter is in memory. Production runs Gunicorn with **one** Uvicorn worker (for supervision and graceful restarts). More workers need sticky sessions; that is documented, not configured.

**D8. Redaction also applies to the text sent to Gemini.** §6.7 asks for redaction before storage and logging. The bot never needs an Aadhaar or mobile number, so the current message is redacted before the model call too. Patterns: 12 digits (Aadhaar-like, with or without spaces/hyphens in 4-4-4 groups), Indian mobiles (optional `+91`/`0`, 10 digits starting 6–9), and any other run of 9 or more digits. Amounts like "₹5,000" and years are not affected.

**D9. Failed, cancelled and superseded turns.** If a reply fails or the user presses Stop, the user message and any partial reply are stored, with `error_code` set (`CANCELLED` is an internal storage marker only, never sent to clients). The **whole turn** is excluded from the history sent to the model. Regenerate works only on the last assistant message and sets `superseded = 1` on the old one.

**D10. The old `history` field** in `POST /api/chat` bodies is accepted and ignored, so old clients don't break. Server-side history is authoritative.

**D11. Admin auth responses.** No `ADMIN_API_KEY` configured → 404 on all admin routes. Header missing → 401. Wrong key → 403. Comparison uses `hmac.compare_digest`.

**D12. Sample knowledge files.** They use real scheme **names and ids** (PMMVY, JSY, JSSK, PMSMA, PICME/RCH) so that aliases, tests and the file layout are ready for real content. **Every value is an obvious placeholder**, e.g. `<<SAMPLE: amount to be supplied>>`, with no realistic numbers, dates, URLs or phone numbers. Each chunk carries `[SAMPLE DATA]`, and the prompt tells the model to say such details are not yet verified. `lint_kb.py` fails if a sample file contains a phone number or URL.

**D13. Translations.** Fixed UI and fallback strings (welcome text, starters, error messages, footer, fallback apology) are drafted in English and Tamil (originally 8 languages; narrowed to en + ta) and marked **"needs native-speaker review"** in `domain.py`. Tamil and Hindi aliases in the sample files get the same mark. UI chrome strings (Send, Stop, Copy…) go in `app/ui/strings.py`, which is one small file added to the §5 layout.

**D14. Helplines and emergency numbers** are `TODO` placeholders until you supply them (§13). Until then the emergency text says "contact local emergency services or go to the nearest hospital", with no numbers.

**D15. Prompt template.** `{{placeholder}}` syntax with a tiny renderer that **fails on any unresolved placeholder** (tested). No Jinja for the prompt.

**D16. Python 3.12** is the target (Docker `python:3.12-slim`). This sandbox has 3.13, so code avoids anything that differs between the two.

---

## 7. NiceGUI against §7 (and §6.8)

| Requirement | Verdict | How |
|---|---|---|
| Responsive 360 px, light/dark | OK | Quasar/Tailwind classes, `ui.dark_mode(None)` follows the system setting. |
| Header, language select, New chat, Delete | OK | |
| Welcome + starter chips from `/api/config` | OK | Fetched through the D2 client. |
| Sanitised Markdown | OK | markdown-it-py with raw HTML **disabled** and its built-in link validation (blocks `javascript:`, `data:`, etc.), rendered into `ui.html`. Tested with injection strings. |
| Streaming caret, smart auto-scroll | OK, with a small JS helper | Auto-scroll stops when the user scrolls up (a ~20-line script). |
| Sources expander, copy, thumbs, Regenerate, Stop | OK | `ui.expansion`, `ui.clipboard`, buttons. |
| Enter to send, Shift+Enter newline, counter | OK | Vue key modifiers on the textarea. |
| Rehydration after refresh | OK | `sessionId` in `app.storage.browser`, then `GET /api/sessions/{id}/messages`. |
| States: empty, typing, error, rate-limited | OK | Typed message restored into the composer on any error. |
| **Offline** | **Partial** | NiceGUI is driven over a websocket. When offline, nothing server-side can run. The state shown is NiceGUI's reconnect overlay, restyled and translated. Typed text stays in the textarea. A client-rendered app could do better here. |
| Indic fonts | OK (changed) | Noto-first CSS font stack using fonts installed on the device. Self-hosted font files weren't added, because they couldn't be downloaded here (see FINAL_REVIEW). |
| Accessibility: labels, keyboard, focus, ARIA live | OK | `aria-live="polite"` region updated per chunk batch, not per token, to avoid flooding screen readers. |
| **CSP (§6.8)** | **Weaker on the UI page** | NiceGUI's Vue/Quasar runtime needs `'unsafe-inline'` and `'unsafe-eval'` for scripts. API responses get a strict policy (`default-src 'none'; frame-ancestors 'none'`). The UI page gets the tightest policy NiceGUI tolerates. |

**Conclusion:** NiceGUI meets §7. The two weak points are offline handling and CSP strictness (the latter is §6.8, not §7). I am **keeping NiceGUI** as specified. If either point matters more than having a pure-Python UI, Jinja2 + HTMX (plus a small SSE script) would fix both. That is your call; I won't switch on my own.

---

## 8. Risks and blockers

1. **This build environment can't install packages.** FastAPI, NiceGUI, google-genai, pydantic-settings, httpx, pytest-asyncio and gunicorn are not installed, and pip has no network access. Installed and usable: pydantic 2, PyYAML, markdown-it-py, Starlette, Uvicorn, pytest, ruff, mypy.
   - The Phase 2 core (loader, index, retriever, prompt builder, history, errors, fake provider, `lint_kb.py`) can be written **and tested here** without FastAPI.
   - Phases 3–4 (FastAPI routes, NiceGUI, the real Gemini adapter) **cannot be run here**. They could be written, but not tested.
   - Fix: allow package installs in your network settings and continue in a **new chat** (the current one may not pick up the change). This file plus the reference folder carry everything needed.
2. **NiceGUI 4.0 is days old.** There is some risk of API changes against the docs. Mitigated by pinning and a UI smoke test.
3. **SQLite = single host.** That is fine for the stated scope. The PostgreSQL move is documented behind the same repository protocol.

---

## 9. Phases and exit criteria

| Phase | Builds | Exit criteria |
|---|---|---|
| 2 Core | `config`, `domain`, prompt template + builder, `LlmProvider` + fake + Gemini adapter, loader/index/keyword retriever, errors, `lint_kb.py`, `check_kb.py`, `_TEMPLATE.md`, KB `README.md`, 5 sample scheme files + 3 general files | Loader, hot-reload, retrieval (EN/TA/HI), prompt-snapshot and history tests pass; ruff + mypy clean on what exists. |
| 3 Storage + API | SQLite + memory repos (migrations, WAL, TTL task), all §6.3 endpoints, SSE with heartbeat and disconnect abort, rate limits, security headers, CORS, body limit, redaction, JSON logging | §10 API, storage, resilience, rate-limit and redaction tests pass; `firebase-admin` absence test passes. |
| 4 UI | NiceGUI page, components, streaming client (D2), all states | UI smoke tests: page loads, config consumed, starters render, refresh rehydrates. |
| 5 Hardening | Coverage ≥ 85% on the listed packages, Dockerfile (multi-stage, non-root), compose, README, `ARCHITECTURE.md` (Mermaid + SSE contract), `CHANGING_DOMAIN.md`, `export_firestore_to_md.py` | Final §3 / §9 checklist, run commands, list of what you must provide. |

---

## 9a. Phase 2 log (core) — done

**Built:** `app/config.py`, `app/domain.py`, `app/prompts/system.v1.md` + `builder.py`,
`app/llm/{base,fake,gemini}.py`, `app/knowledge/{base,text,loader,index,keyword}.py`,
`app/services/{errors,history,tokens}.py`, `scripts/{lint_kb,check_kb}.py`,
`knowledge/_TEMPLATE.md`, `knowledge/README.md`, 5 sample scheme files + 3 sample general
files, `.env.example`, `.gitignore`, `pyproject.toml`, 106 tests.

**Results in this sandbox:** 106 passed, 1 skipped (`Settings` test needs `pydantic-settings`);
`ruff check` and `ruff format --check` are clean; `mypy --strict` on `app/` is clean apart from
`app/config.py`, which can't be type-checked until `pydantic-settings` is installed.
Coverage isn't measured yet because `coverage`/`pytest-cov` isn't installed.

**Deviations and additions (all reversible):**
1. **Async tests use AnyIO's pytest plugin** (`@pytest.mark.anyio`) instead of pytest-asyncio.
   AnyIO ships with Starlette/FastAPI, so there's one fewer dependency, and it runs here.
2. **New env var `PROMPT_MAX_INPUT_TOKENS`** (default 12000) for §6.5's total token budget.
   It's listed in `.env.example`, and a test checks that `.env.example` and `Settings` match exactly.
3. **Two small helper modules** were added beyond §5: `app/knowledge/text.py` (Unicode tokeniser,
   needed because Python's `\w` splits Tamil words at vowel signs) and
   `app/services/tokens.py` (the D5 estimator).
4. **"Whole KB fits → send everything" also applies when nothing matched.** §6.4 lists both
   "send all if it fits" and "no match → nothing". I read "do not send arbitrary files" as
   forbidding a *partial* arbitrary subset (the old `scanned <= 30` bug). Sending the complete
   knowledge base when it fits is the old small-DB behaviour, and it helps with paraphrased
   questions that keyword matching misses. When the KB doesn't fit, a no-match returns nothing.
5. **Heading keywords live in `domain.py`** (English and Tamil), so changing domain
   doesn't touch the retriever.
6. **Hot reload keeps the last good version per file.** If an edit breaks one file, that file's
   previous chunks keep being served and both problems are reported. If the whole directory
   becomes unreadable, the previous snapshot is kept.
7. **Gemini retry happens only before the first chunk is streamed.** Retrying mid-stream would
   duplicate text. A 429 is never retried. Backoff is 1 s, then 2 s, plus jitter, with at most
   3 attempts.

**Not yet pinned:** `requirements.txt`. Exact versions get pinned in Phase 3, when FastAPI,
NiceGUI and google-genai are added and can be verified.

---

## 9b. Phases 3–5 log — done (partly untested in the sandbox)

**Built:** `app/storage/{base,sqlite_repo,memory_repo}.py`,
`app/security/{redaction,rate_limit,cookies}.py`, `app/logging_setup.py`,
`app/services/chat_service.py`, `app/container.py`, `app/main.py`,
`app/api/{middleware,schemas,sse,deps,chat,sessions,feedback,meta}.py`,
`app/ui/{client,strings,markdown,components,page}.py`, Dockerfile, compose file,
requirements, `scripts/export_firestore_to_md.py`, README, ARCHITECTURE,
CHANGING_DOMAIN and FINAL_REVIEW.

**Tested in the sandbox:** everything that doesn't import FastAPI or NiceGUI, including
the middleware, SSE and in-process client against a plain Starlette app, and an end-to-end
"Stop aborts the provider" check. The FastAPI routes and NiceGUI page are written and have
tests (`tests/test_api.py`, `tests/test_ui.py`) that run once those packages are installed.
See `docs/FINAL_REVIEW.md`.

**More additions and deviations:**
8. New modules beyond §5: `app/container.py` (wiring), `app/logging_setup.py`,
   `app/security/cookies.py`, `app/api/{middleware,schemas,sse}.py`, `app/ui/{strings,markdown}.py`.
   Admin routes live in `app/api/meta.py`.
9. The `sessions.client_id` column was added (D1).
10. **Assistant text is stored unredacted** (only user text is redacted). The model never
    sees the user's raw numbers, and redacting answers would break legitimate helpline
    numbers quoted from the knowledge files.
11. The **daily cap counts assistant replies**, which includes regenerations.
12. The `/api/chat` body's length limit is configurable, so it's checked in the service
    (422 `INVALID_INPUT`). The Pydantic model has a hard ceiling of 20,000 characters.
13. **Failure frame order** is `start` → `chunk` (fallback text) → `error`, with no `done`.
14. **UI session id** is kept in NiceGUI `app.storage.user` (server-side, keyed by a signed
    browser cookie, stored under `data/.nicegui` in Docker). It's used for rehydration on refresh.
15. **Pins:** exact versions for direct dependencies only. Freeze the full tree after the
    first successful install. A conflict between pins is possible because they couldn't be
    installed together here.
16. **Gunicorn uses the `uvicorn-worker` package** (`uvicorn_worker.UvicornWorker`), the
    maintained successor to `uvicorn.workers`.

---

## 10. What I need from you (now or before final delivery)

- **To continue past Phase 2 core with tests:** package installs enabled (see §8.1).
- Optional: `chatbot-architecture-analysis.md`.
- Before final delivery (Part C): `GEMINI_API_KEY`, real scheme content, official helpline/portal/emergency numbers, final name, colours and disclaimer wording, deployment target and public domain, and confirmation that a single host with a local volume is acceptable.
