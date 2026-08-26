# Skywatch — one image, three roles (see compose.yaml):
#   watch      : snapshots when models publish new cycles; briefs at anchors / on change
#   serve      : dashboard + alerts over HTTP
#   stormwatch : real-time lightning watcher
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    SKYWATCH_DATA_DIR=/data \
    SKYWATCH_CONFIG=/app/config.yaml \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY config.yaml ./
COPY prompts ./prompts
COPY templates ./templates

VOLUME ["/data"]
# serve inside the container binds all interfaces; compose publishes to the host IP you choose
ENV SKYWATCH_SERVE_HOST=0.0.0.0
EXPOSE 8092

ENTRYPOINT ["skywatch"]
CMD ["--help"]
