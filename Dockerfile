# Python 3.12 slim base, uv for dependency management
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra redis

# Copy source and install package
COPY yomai/ ./yomai/
COPY README.md ./
RUN uv sync --frozen --extra redis

# ── Runtime stage ─────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/yomai /app/yomai
COPY pyproject.toml ./

ENV PATH="/app/.venv/bin:$PATH"
ENV YOMAI_ENV=production
ENV YOMAI_LOG_FORMAT=json

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/__yomai__/health')"

ENTRYPOINT ["python", "-m", "yomai.cli.main"]
CMD ["serve"]
