FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src/ src/
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 mcp \
    && mkdir -p /app/logs \
    && chown mcp /app/logs

USER mcp
ENV PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000
EXPOSE 8000
ENTRYPOINT ["nifipilot-plus"]
