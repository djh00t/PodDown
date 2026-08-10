"""Contract tests for packaged API and Temporal worker entrypoints."""

import tomllib
from pathlib import Path

import pytest
import yaml

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
