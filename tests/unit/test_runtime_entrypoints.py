"""Contract tests for packaged API and Temporal worker entrypoints."""

import asyncio
import tomllib
from pathlib import Path

import pytest
import yaml

import poddown.runtime as runtime
from poddown.runtime import RuntimeConfigurationError, worker_settings


def test_compose_commands_match_packaged_entrypoints_and_dockerfile() -> None:
    project = Path(__file__).parents[2]
    compose = yaml.safe_load((project / "compose.yaml").read_text())
    package = tomllib.loads((project / "pyproject.toml").read_text())
    assert (project / "Dockerfile").exists()
    scripts = package["project"]["scripts"]
    assert scripts["poddown-api"] == "poddown.runtime:api_main"
    assert scripts["poddown-worker"] == "poddown.runtime:worker_main"
    assert compose["services"]["api"]["command"] == ["poddown-api"]
    assert compose["services"]["worker"]["command"] == ["poddown-worker"]


def test_container_contract_exposes_entrypoints_and_worker_readiness_wiring() -> None:
    project = Path(__file__).parents[2]
    dockerfile = (project / "Dockerfile").read_text()
    worker = yaml.safe_load((project / "compose.yaml").read_text())["services"][
        "worker"
    ]
    assert 'ENV PATH="/app/.venv/bin:$PATH"' in dockerfile
    assert worker["environment"]["PODDOWN_TEMPORAL_ADDRESS"] == "temporal:7233"
    assert worker["environment"]["PODDOWN_TEMPORAL_NAMESPACE"] == "default"
    assert worker["environment"]["PODDOWN_TEMPORAL_TASK_QUEUE"] == "poddown-default"
    assert worker["healthcheck"]["test"] == [
        "CMD-SHELL",
        "test -f /tmp/poddown-worker-ready",
    ]
    assert "PODDOWN_WORKER_READY_FILE" in dockerfile


def test_worker_entrypoint_requires_explicit_temporal_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PODDOWN_TEMPORAL_ADDRESS", raising=False)
    monkeypatch.delenv("PODDOWN_TEMPORAL_TASK_QUEUE", raising=False)
    with pytest.raises(RuntimeConfigurationError, match="PODDOWN_TEMPORAL_ADDRESS"):
        worker_settings()


def test_worker_settings_reads_temporal_endpoint_and_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")
    settings = worker_settings()
    assert settings.address == "temporal:7233"
    assert settings.task_queue == "poddown-default"


def test_worker_settings_exposes_configured_readiness_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")
    monkeypatch.setenv("PODDOWN_WORKER_READY_FILE", "/tmp/worker.ready")
    assert worker_settings().ready_file == "/tmp/worker.ready"


def test_worker_readiness_marker_tracks_started_worker_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "worker.ready"
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")
    monkeypatch.setenv("PODDOWN_WORKER_READY_FILE", str(marker))

    class FakeClient:
        @classmethod
        async def connect(cls, address: str, namespace: str) -> object:
            return object()

    class FakeWorker:
        is_running = False

        def __init__(self, *_args, **_kwargs) -> None:
            self.is_running = False

        async def __aenter__(self):
            self.is_running = True
            return self

        async def __aexit__(self, *_args) -> None:
            self.is_running = False

    monkeypatch.setattr(runtime, "Client", FakeClient)
    monkeypatch.setattr(runtime, "Worker", FakeWorker)

    async def exercise() -> None:
        task = asyncio.create_task(runtime._run_worker())
        for _ in range(20):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert not task.done()
        assert marker.read_text() == "ready\n"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not marker.exists()

    asyncio.run(exercise())
