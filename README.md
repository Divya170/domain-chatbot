# Pregnancy Schemes Assistant — FastAPI + NiceGUI

This is a domain chatbot for Indian maternity schemes. It answers from **Markdown knowledge
files**, keeps chat history in **SQLite**, streams answers from **Gemini**, and serves a
pure-Python **NiceGUI** chat page in English and Tamil. Everything runs as one process.

> ⚠️ The knowledge files are `status: draft`: written from official documents but not yet
> checked by a person. Before going live, review each file against its source and set
> `status: verified`, `source_url` and `last_verified` (see "What you must provide").

## Quick start (local)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # set GEMINI_API_KEY, GEMINI_MODEL, SESSION_SECRET
uvicorn app.main:create_app --factory --reload --port 8000
```

Open http://localhost:8000. Health check: http://localhost:8000/health.

If pip reports a version conflict between the pins, install the same package names
without versions, run the tests, then `pip freeze > requirements.lock` and pin from that.
(The pins were chosen from current releases but could not be installed together in the
build environment.)

## Environment variables

Every variable is documented in `.env.example`, and a test keeps that file and
`app/config.py` in sync. The most important ones:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Server-side only. Without it, chat returns `NOT_CONFIGURED`. |
| `GEMINI_MODEL` | **Required, with no default in code.** Use a current id from https://ai.google.dev/gemini-api/docs/models. It is checked at startup (`/health` → `geminiModelVerified`). |
| `SESSION_SECRET` | Signs the client cookie and NiceGUI storage. Required in production. |
| `ADMIN_API_KEY` | Enables `/api/admin/kb/*` (send it as the `X-Admin-Key` header). |
| `STORAGE_BACKEND` | `sqlite` (default) or `memory`. |
| `KB_RELOAD_CHECK_SECONDS` | How often edited knowledge files are picked up (0 = off). |

These deployment-only variables are read by Gunicorn and NiceGUI, not by the app:
`FORWARDED_ALLOW_IPS` (your proxy's IP) and `NICEGUI_STORAGE_PATH` (set in the Dockerfile).

## Tests and quality checks

```bash
pytest --cov=app --cov-report=term-missing
ruff check . && ruff format --check .
mypy                      # strict on app/
python scripts/lint_kb.py # validate knowledge files (exits 1 on errors)
```

Async tests use AnyIO's pytest plugin, which ships with AnyIO, so pytest-asyncio isn't needed.

## Knowledge files

The files live in `knowledge/`, one topic per file, with YAML front-matter and fixed `##`
sections. See `knowledge/README.md` and `knowledge/_TEMPLATE.md`.

```bash
python scripts/lint_kb.py                                        # validate
python scripts/check_kb.py "PMMVY documents"                     # what the model receives
python scripts/check_kb.py "மாத்ரு வந்தனா ஆவணங்கள்" --language ta
```

Edits are picked up automatically, with no restart needed. A broken edit keeps the last
good version of that file and is listed at `GET /api/admin/kb/status`.

Migrating from the old Firestore database is an optional one-off:
`pip install firebase-admin && python scripts/export_firestore_to_md.py --key key.json`.
It writes `status: draft` files that must be reviewed by a person.

## Docker

```bash
cp .env.example .env    # fill in values
docker compose up --build -d
```

* The image is multi-stage, runs as a non-root user (uid 10001) and has a health check.
* `knowledge/` is bind-mounted read-only, so you can edit files on the host and they reload live.
* The `chat-data` volume holds `data/chat.db` and NiceGUI's per-browser storage.

## Deployment notes

* **Behind nginx**, turn buffering off for SSE and forward the client IP:

  ```nginx
  location / {
      proxy_pass http://127.0.0.1:8000;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;       # NiceGUI websocket
      proxy_set_header Connection "upgrade";
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_buffering off;                          # SSE
      proxy_read_timeout 300s;
      gzip off;
  }
  ```

  Set `FORWARDED_ALLOW_IPS` to nginx's IP so rate limits see real client IPs.
* **Use one worker.** NiceGUI keeps UI state per process and the per-minute rate limiter
  is in memory. Running more workers needs sticky sessions.
* **SQLite suits one host with one volume.** If you need several machines, implement
  `ChatRepository` for PostgreSQL (`app/storage/base.py`); nothing else changes.
* Use `APP_ENV=production` for HSTS, `Secure` cookies and no `/docs`.

## API

| Method + path | Purpose |
|---|---|
| `POST /api/chat/stream` | SSE stream. Body `{message, language, sessionId?}` |
| `POST /api/chat` | Same pipeline, JSON `{reply, messageId, sessionId, sources, usage, error?}` |
| `POST /api/chat/regenerate` | `{sessionId, messageId}`; regenerates the last answer |
| `GET /api/sessions/{id}/messages` | Rehydrates a conversation (owner only) |
| `DELETE /api/sessions/{id}` | Deletes a conversation (owner only) |
| `POST /api/messages/{id}/feedback` | `{rating: "up"|"down", note?}` |
| `GET /api/config` | Title, languages, welcome text, starters, disclaimer |
| `GET /health` | Booleans: Gemini configured/verified, knowledge loaded, storage ok |
| `POST /api/admin/kb/reload`, `GET /api/admin/kb/status` | `X-Admin-Key` required. 404 if no key is configured. |

The SSE contract and request flow are in `docs/ARCHITECTURE.md`.

## What you must provide

See the final list in `docs/FINAL_REVIEW.md`: Gemini key and model id, real scheme
content, official helplines, review of the translations, branding, and the deployment target.
