FROM python:3.13-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg espeak-ng libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY skills ./skills
COPY integrations ./integrations
RUN pip install --no-cache-dir uv==0.8.17 \
    && uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH"
ENV PODDOWN_WORKER_READY_FILE="/tmp/poddown-worker-ready"

ENTRYPOINT []
