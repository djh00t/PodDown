"""BDD bindings for explicit API and Temporal runtime composition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import poddown.runtime as runtime
from poddown.api.temporal_dispatcher import (
    TemporalClientTransport,
    TemporalCommandRequest,
)
from poddown.runtime import RuntimeConfigurationError, api_runtime_settings

scenario_file = "../features/runtime_composition.feature"
scenarios(scenario_file)


@given("an API environment without a SQLite database path")
def api_environment_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PODDOWN_SQLITE_PATH", raising=False)
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")


@given("an API environment with a SQLite database path and Temporal settings")
def api_environment_with_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PODDOWN_SQLITE_PATH", str(tmp_path / "poddown.sqlite3"))
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_NAMESPACE", "default")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")


@given("an API environment with a PostgreSQL DSN and Temporal settings")
def api_environment_with_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PODDOWN_SQLITE_PATH", raising=False)
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")


@when("API runtime settings are loaded")
def load_api_settings(context: dict[str, Any]) -> None:
    try:
        context.values["settings"] = api_runtime_settings()
    except RuntimeConfigurationError as error:
        context.values["error"] = error


@then(parsers.parse('runtime configuration fails for "{name}"'))
def runtime_configuration_fails(context: dict[str, Any], name: str) -> None:
    assert isinstance(context.values.get("error"), RuntimeConfigurationError)
    assert name in str(context.values["error"])


@then("the API runtime uses the configured durable database and task queue")
def api_runtime_uses_durable_ports(context: dict[str, Any]) -> None:
    settings = context.values["settings"]
    assert settings.database_path.exists() is False
    assert settings.task_queue == "poddown-default"
    assert settings.temporal_address == "temporal:7233"


@then("the API runtime selects PostgreSQL without a SQLite path")
def api_runtime_selects_postgres(context: dict[str, Any]) -> None:
    settings = context.values["settings"]
    assert settings.database_path is None
    assert settings.postgres_dsn == "postgresql://poddown@postgres/poddown"


@given("a worker configured for PostgreSQL publication receipts")
def worker_publication_receipts(
    context: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PODDOWN_PUBLICATION_MODE", "filesystem")
    monkeypatch.setenv("PODDOWN_RENDER_DATA_DIR", str(tmp_path / "render-data"))
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    sentinel_factory = object()
    sentinel_store = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        runtime, "postgres_connection_factory", lambda _dsn: sentinel_factory
    )
    monkeypatch.setattr(runtime, "initialize_postgres", lambda _factory: ())
    monkeypatch.setattr(
        runtime,
        "PostgresPublicationReceiptRepository",
        lambda _factory: sentinel_store,
    )

    def capture_activity(service: object, **_kwargs: object) -> object:
        captured["service"] = service
        return lambda: None

    monkeypatch.setattr(runtime, "build_durable_publication_activity", capture_activity)
    context.values["receipt_store"] = sentinel_store
    context.values["captured"] = captured


@when("worker publication composition is built")
def build_worker_publication(context: dict[str, Any]) -> None:
    context.values["activity"] = runtime._durable_publication_activity()


@then("the publication service uses the durable receipt store")
def publication_service_uses_durable_receipts(context: dict[str, Any]) -> None:
    service = context.values["captured"]["service"]
    assert service.receipt_store is context.values["receipt_store"]


class RecordingTemporalClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def start_workflow(
        self, workflow: object, arg: object, **kwargs: object
    ) -> None:
        self.calls.append({"workflow": workflow, "arg": arg, **kwargs})


@given("a recording Temporal client factory")
def recording_temporal_client(context: dict[str, Any]) -> None:
    client = RecordingTemporalClient()

    async def factory(_address: str, *, namespace: str) -> RecordingTemporalClient:
        assert namespace == "default"
        return client

    context.values["client"] = client
    context.values["transport"] = TemporalClientTransport(
        "temporal:7233", namespace="default", client_factory=factory
    )


@when("the Temporal transport starts a command request")
def start_temporal_request(context: dict[str, Any]) -> None:
    request = TemporalCommandRequest(
        workflow_id="poddown-command-test",
        task_queue="poddown-default",
        payload=json.dumps({"episode_id": "01986e76-4ec6-7a8f-8000-000000000001"}),
    )
    context.values["transport"].start_workflow(request)


@then("the Temporal client receives the immutable workflow payload")
def temporal_client_receives_payload(context: dict[str, Any]) -> None:
    calls = context.values["client"].calls
    assert len(calls) == 1
    assert calls[0]["arg"] == json.dumps(
        {"episode_id": "01986e76-4ec6-7a8f-8000-000000000001"}
    )
    assert calls[0]["id"] == "poddown-command-test"
    assert calls[0]["task_queue"] == "poddown-default"


@given("the Compose image runtime contract")
def compose_image_contract(context: dict[str, Any]) -> None:
    context.values["dockerfile"] = (Path(__file__).parents[2] / "Dockerfile").read_text(
        encoding="utf-8"
    )


@when("the container media prerequisites are inspected")
def inspect_container_media_prerequisites(context: dict[str, Any]) -> None:
    context.values["media_prerequisites_checked"] = True


@then("the image installs FFmpeg and a Linux speech engine")
def image_installs_media_prerequisites(context: dict[str, Any]) -> None:
    dockerfile = context.values["dockerfile"]
    assert context.values["media_prerequisites_checked"] is True
    assert "ffmpeg" in dockerfile
    assert "espeak-ng" in dockerfile
