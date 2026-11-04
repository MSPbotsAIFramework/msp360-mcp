# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS production
# curl is used by the container health check (the slim image has neither curl nor wget).
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1001 app && useradd -u 1001 -g app -s /usr/sbin/nologin -m app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
COPY LICENSE NOTICE ./
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8080
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/health || exit 1
CMD ["python", "-m", "msp360_mcp"]

LABEL org.opencontainers.image.title="msp360-mcp" \
      org.opencontainers.image.description="MSP360 Managed Backup Service MCP server for the MSPbots Agent Platform" \
      org.opencontainers.image.vendor="MSPbots.ai" \
      org.opencontainers.image.licenses="Apache-2.0"
