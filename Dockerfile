# syntax=docker/dockerfile:1

# Resumidor: multi-stage. uv y las herramientas de build quedan en el builder;
# el runtime solo lleva Python, el venv y el código.
ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:0.11.4 AS uv-bin

# ---------- Build ----------
FROM python:${PYTHON_VERSION}-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=uv-bin /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./

# Solo dependencias de producción, exactamente las del lock; el proyecto se ejecuta desde /app/src.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------- Runtime ----------
FROM python:${PYTHON_VERSION}-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="pdf-extractext-resumidor" \
      org.opencontainers.image.description="Microservicio de resumen vía LLM (API HTTP)" \
      org.opencontainers.image.source="https://github.com/Zindorg/pdf-extractext-resumidor" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Usuario de sistema sin shell ni home.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
               --home-dir /nonexistent --shell /usr/sbin/nologin app \
    # Quita setuid/setgid del runtime.
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

WORKDIR /app
# venv y código son de root y de solo lectura para el proceso (no escribe en /app).
COPY --from=build --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root src ./src

USER 10001:10001

# Arranca directamente el servidor HTTP (FastAPI)
CMD ["/app/.venv/bin/python", "-m", "src.presentation.http.main"]