# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.4 AS uv-bin
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

COPY --from=uv-bin /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src

USER app

CMD ["/app/.venv/bin/python", "-m", "src.presentation.worker.main"]
