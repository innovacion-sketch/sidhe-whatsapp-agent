# --- Etapa 1: dependencias con uv ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

# --- Etapa 2: imagen final ---
FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    TZ=America/Mexico_City \
    PYTHONUNBUFFERED=1

COPY --from=builder /app /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "sidhe_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
