# ---- build stage: install dependencies into a virtualenv -------------------------
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt

# ---- runtime stage: no build tools, non-root user ---------------------------------
FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    SQLITE_PATH=/app/data/chat.db \
    NICEGUI_STORAGE_PATH=/app/data/.nicegui \
    PORT=8000
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY app ./app
COPY knowledge ./knowledge
COPY scripts/__init__.py scripts/lint_kb.py scripts/check_kb.py ./scripts/
RUN mkdir -p /app/data && chown -R app:app /app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)"
# One worker: NiceGUI keeps per-browser UI state in memory and the per-minute rate
# limiter is in memory (see docs/PLAN.md D7). Gunicorn supervises and restarts it.
# FORWARDED_ALLOW_IPS = IP of your reverse proxy, so client IPs are real.
CMD ["sh", "-c", "exec gunicorn 'app.main:create_app()' -k uvicorn_worker.UvicornWorker -w 1 -b 0.0.0.0:${PORT} --timeout 120 --graceful-timeout 30 --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1}"]
