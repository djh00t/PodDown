"""Packaged local API and Temporal worker runtime entrypoints."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

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
    )


def api_main() -> None:
    """Serve the existing FastAPI app through uvicorn."""

    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("PODDOWN_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("PODDOWN_API_PORT", "8000")),
    )


async def _run_worker() -> None:
    settings = worker_settings()
    client = await Client.connect(settings.address, namespace=settings.namespace)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[EpisodeRenderWorkflow],
        activities=[render_segment_activity],
    )
    await worker.run()


def worker_main() -> None:
    """Run the existing Temporal workflow/activity contracts."""

    asyncio.run(_run_worker())


__all__ = [
    "RuntimeConfigurationError",
    "WorkerSettings",
    "api_main",
    "worker_main",
    "worker_settings",
]
