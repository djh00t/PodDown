FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY skills ./skills
RUN pip install --no-cache-dir uv==0.8.17 \
    && uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH"
ENV PODDOWN_WORKER_READY_FILE="/tmp/poddown-worker-ready"

ENTRYPOINT []
