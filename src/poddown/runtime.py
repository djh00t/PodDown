"""Packaged local API and Temporal worker runtime entrypoints."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from temporalio.client import Client
from temporalio.worker import Worker

from poddown.api import create_app
from poddown.audio.workflow import EpisodeRenderWorkflow, render_segment_activity


class RuntimeConfigurationError(ValueError):
    """Required runtime configuration is absent or malformed."""


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """Explicit Temporal endpoint, namespace, and task queue settings."""

    address: str
    namespace: str
    task_queue: str
    ready_file: str


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeConfigurationError(f"{name} is required")
    return value


def worker_settings() -> WorkerSettings:
    """Read worker settings and fail closed before any network connection."""

    return WorkerSettings(
        address=_required("PODDOWN_TEMPORAL_ADDRESS"),
        namespace=os.environ.get("PODDOWN_TEMPORAL_NAMESPACE", "default").strip()
        or "default",
        task_queue=_required("PODDOWN_TEMPORAL_TASK_QUEUE"),
        ready_file=os.environ.get(
            "PODDOWN_WORKER_READY_FILE", "/tmp/poddown-worker-ready"
        ).strip()
        or "/tmp/poddown-worker-ready",
    )


def _tcp_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _configured_probe(value: str, default_port: int) -> Callable[[], bool]:
    parsed = urlparse(value if "://" in value else f"//{value}")
    host = parsed.hostname
    port = parsed.port or default_port
    if not host:
        return lambda: False
    return lambda: _tcp_probe(host, port)


def runtime_dependency_probes() -> dict[str, Callable[[], bool]]:
    """Build bounded TCP probes; absent configuration is explicitly unhealthy."""

    endpoints = {
        "postgres": (os.environ.get("PODDOWN_POSTGRES_HOST", ""), 5432),
        "temporal": (os.environ.get("PODDOWN_TEMPORAL_ADDRESS", ""), 7233),
        "nats": (os.environ.get("PODDOWN_NATS_HOST", ""), 4222),
        "minio": (os.environ.get("PODDOWN_MINIO_ENDPOINT", ""), 9000),
    }
    return {
        name: _configured_probe(value, port) if value.strip() else (lambda: False)
        for name, (value, port) in endpoints.items()
    }


def api_main() -> None:
    """Serve the existing FastAPI app through uvicorn."""

    import uvicorn

    uvicorn.run(
        create_app(health_probes=runtime_dependency_probes()),
        host=os.environ.get("PODDOWN_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("PODDOWN_API_PORT", "8000")),
    )


async def _run_worker() -> None:
    settings = worker_settings()
    ready_file = Path(settings.ready_file)
    ready_file.unlink(missing_ok=True)
    client = await Client.connect(settings.address, namespace=settings.namespace)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[EpisodeRenderWorkflow],
        activities=[render_segment_activity],
    )
    try:
        async with worker:
            await asyncio.sleep(0.1)
            if not worker.is_running:
                raise RuntimeConfigurationError("Temporal worker did not become ready")
            ready_file.write_text("ready\n")
            await asyncio.Future()
    finally:
        ready_file.unlink(missing_ok=True)


def worker_main() -> None:
    """Run the existing Temporal workflow/activity contracts."""

    asyncio.run(_run_worker())


__all__ = [
    "RuntimeConfigurationError",
    "WorkerSettings",
    "api_main",
    "worker_main",
    "runtime_dependency_probes",
    "worker_settings",
]
