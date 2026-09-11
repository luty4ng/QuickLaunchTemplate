# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 - build the web client. Its output is the same bundle that the
# desktop and Android jobs package, so all three clients share one UI.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Relative asset URLs so the identical bundle also works inside the desktop
# shell (file://) and the Android WebView.
ENV VITE_WEB_BASE=./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 - resolve Python dependencies into a throwaway prefix. Build tools
# stay in this stage so the runtime image never carries a compiler.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /wheels
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 3 - runtime. Non-root, no build tools, one health check.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production \
    AUTO_MIGRATE=false \
    WEB_DIST=/app/web/dist \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY --from=web /web/dist /app/web/dist
RUN chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

# `/api/health` also proves the database is reachable, unlike a bare TCP check.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

WORKDIR /app/backend
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --proxy-headers"]
