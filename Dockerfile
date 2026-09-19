FROM python:3.12-slim AS base

WORKDIR /app

RUN groupadd -r agent && useradd -r -g agent -d /app agent

COPY pyproject.toml ./
RUN pip install --no-cache-dir . 2>/dev/null || pip install --no-cache-dir \
    "google-adk>=2.6.0" \
    "fastapi>=0.115.0" \
    "httpx>=0.27.0" \
    "mcp>=2.2.0" \
    "pydantic>=2.0.0" \
    "pydantic-settings>=2.0.0" \
    "pyyaml>=6.0" \
    "structlog>=24.0.0" \
    "uvicorn[standard]>=0.32.0"

COPY ford_platform_agent/ ./ford_platform_agent/
COPY knowledge/ ./knowledge/

RUN chown -R agent:agent /app
USER agent

EXPOSE 8080

ENV PORT=8080
ENV HOST=0.0.0.0

CMD ["ford-agent", "chat"]
