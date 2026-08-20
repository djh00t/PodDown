"""Contract tests for packaged API and Temporal worker entrypoints."""

import asyncio
import json
import tomllib
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from temporalio.exceptions import ApplicationError

import poddown.runtime as runtime
from poddown.nats_outbox import OutboxRelayWorkflow
from poddown.persistence import SQLiteUsageLedger
from poddown.runtime import (
    ApiRuntimeSettings,
    RuntimeConfigurationError,
    api_runtime_settings,
    build_api_app,
    runtime_workflow_snapshot_factory,
    worker_settings,
)


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


def test_api_composition_injects_durable_state_temporal_and_approval_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_create_app(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(runtime, "create_app", fake_create_app)
    settings = ApiRuntimeSettings(
        database_path=tmp_path / "poddown.sqlite3",
        temporal_address="temporal:7233",
        temporal_namespace="default",
        task_queue="poddown-default",
    )

    assert build_api_app(settings) is not None
    assert captured["database_path"] == settings.database_path
    assert captured["temporal_task_queue"] == settings.task_queue
    assert captured["approval_repository"].__class__.__name__ == (
        "SQLiteApprovalRepository"
    )
    assert captured["temporal_transport"].__class__.__name__ == (
        "TemporalClientTransport"
    )
    assert captured["require_workflow_snapshot"] is True


def test_runtime_snapshot_factory_is_opt_in_and_uses_reference_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PODDOWN_WORKFLOW_FIXTURE_ROOT", raising=False)
    assert runtime_workflow_snapshot_factory() is None

    monkeypatch.setenv(
        "PODDOWN_WORKFLOW_FIXTURE_ROOT",
        str(Path(__file__).parents[2] / "integrations/reference-demo/v1"),
    )
    factory = runtime_workflow_snapshot_factory()

    assert factory is not None
    assert factory.mode == "host-local"
    assert factory.profile_names == frozenset({"reference-demo-dialogue-v1"})


def test_api_composition_injects_the_explicit_runtime_snapshot_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create_app(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(runtime, "create_app", fake_create_app)
    monkeypatch.setattr(runtime, "runtime_workflow_snapshot_factory", lambda: sentinel)
    settings = ApiRuntimeSettings(
        database_path=tmp_path / "poddown.sqlite3",
        temporal_address="temporal:7233",
        temporal_namespace="default",
        task_queue="poddown-default",
    )

    build_api_app(settings)

    assert captured["workflow_snapshot_factory"] is sentinel


def test_api_composition_injects_signed_resource_ports_for_postgres_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_create_app(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    def connection_factory() -> object:
        return object()

    monkeypatch.setattr(runtime, "create_app", fake_create_app)
    monkeypatch.setattr(
        runtime, "postgres_connection_factory", lambda _dsn: connection_factory
    )
    monkeypatch.setattr(runtime, "initialize_postgres", lambda _factory: None)
    monkeypatch.setattr(
        runtime,
        "_s3_runtime_components",
        lambda *, connection_factory: (object(), object(), object()),
    )
    monkeypatch.setenv(
        "PODDOWN_RESOURCE_LINK_SECRET", "resource-link-test-secret-32-bytes-long"
    )
    monkeypatch.setenv(
        "PODDOWN_RESOURCE_LINK_BASE_URL", "https://resources.example.test"
    )
    monkeypatch.setenv("PODDOWN_RESOURCE_LINK_TTL_SECONDS", "120")
    settings = ApiRuntimeSettings(
        database_path=None,
        postgres_dsn="postgresql://poddown@postgres/poddown",
        temporal_address="temporal:7233",
        temporal_namespace="default",
        task_queue="poddown-default",
    )

    build_api_app(settings)

    assert captured["resource_link_signer"].to_record() == {
        "base_url": "https://resources.example.test",
        "ttl_seconds": 120,
    }
    assert callable(captured["resource_reader"])


def test_worker_s3_publication_requires_durable_store_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S3 publication must fail before constructing a filesystem-only service."""
    monkeypatch.setenv("PODDOWN_PUBLICATION_MODE", "s3")
    monkeypatch.setenv("PODDOWN_RENDER_DATA_DIR", str(tmp_path / "render-data"))
    monkeypatch.delenv("PODDOWN_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("PODDOWN_MINIO_ENDPOINT", raising=False)

    with pytest.raises(RuntimeConfigurationError, match="PODDOWN_POSTGRES_DSN"):
        runtime._durable_publication_activity()


def test_worker_s3_publication_composes_existing_object_and_reference_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The S3 worker path composes MinIO bytes with PostgreSQL references."""
    monkeypatch.setenv("PODDOWN_PUBLICATION_MODE", "s3")
    monkeypatch.setenv("PODDOWN_RENDER_DATA_DIR", str(tmp_path / "render-data"))
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    monkeypatch.setenv("PODDOWN_MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("PODDOWN_MINIO_BUCKET", "poddown")
    monkeypatch.setenv("PODDOWN_MINIO_SECRET_REF", "secret://minio/poddown")
    monkeypatch.setenv("PODDOWN_MINIO_ACCESS_KEY", "local-user")
    monkeypatch.setenv("PODDOWN_MINIO_SECRET_KEY", "local-password")
    monkeypatch.setenv("PODDOWN_MINIO_ALLOW_INSECURE_LOCAL", "1")
    initialized: list[object] = []
    monkeypatch.setattr(runtime, "Boto3S3Transport", lambda *_args: object())
    monkeypatch.setattr(
        runtime,
        "postgres_connection_factory",
        lambda _dsn: lambda: object(),
    )
    monkeypatch.setattr(runtime, "initialize_postgres", initialized.append)

    activity = runtime._durable_publication_activity()

    assert callable(activity)
    assert len(initialized) == 1


def test_worker_filesystem_publication_uses_postgres_receipt_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A configured worker must replay publication receipts across processes."""
    monkeypatch.setenv("PODDOWN_PUBLICATION_MODE", "filesystem")
    monkeypatch.setenv("PODDOWN_RENDER_DATA_DIR", str(tmp_path / "render-data"))
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    sentinel_factory = object()
    sentinel_store = object()
    initialized: list[object] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        runtime,
        "postgres_connection_factory",
        lambda _dsn: sentinel_factory,
    )
    monkeypatch.setattr(runtime, "initialize_postgres", initialized.append)
    monkeypatch.setattr(
        runtime,
        "PostgresPublicationReceiptRepository",
        lambda factory: (sentinel_store, factory)[0],
    )

    def capture_activity(service: object, **_kwargs: object) -> object:
        captured["service"] = service
        return lambda: None

    monkeypatch.setattr(runtime, "build_durable_publication_activity", capture_activity)

    activity = runtime._durable_publication_activity()

    assert callable(activity)
    assert captured["service"].receipt_store is sentinel_store  # type: ignore[union-attr]
    assert initialized == [sentinel_factory]


def test_production_filesystem_publication_requires_postgres_receipts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Production publication must not silently retain receipts in memory."""
    monkeypatch.setenv("PODDOWN_PUBLICATION_MODE", "filesystem")
    monkeypatch.setenv("PODDOWN_RENDER_DATA_DIR", str(tmp_path / "render-data"))
    monkeypatch.setenv(
        "PODDOWN_WORKFLOW_FIXTURE_ROOT",
        str(Path(__file__).parents[2] / "integrations/reference-demo/v1"),
    )
    monkeypatch.delenv("PODDOWN_POSTGRES_DSN", raising=False)

    with pytest.raises(RuntimeConfigurationError, match="publication receipts"):
        runtime._durable_publication_activity(production_output=True)


def test_worker_object_maintenance_requires_explicit_s3_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PODDOWN_OBJECT_MAINTENANCE_ENABLED", "0")
    assert runtime._object_maintenance_activities() == []

    monkeypatch.setenv("PODDOWN_OBJECT_MAINTENANCE_ENABLED", "1")
    monkeypatch.setenv("PODDOWN_PUBLICATION_MODE", "filesystem")
    with pytest.raises(RuntimeConfigurationError, match="PODDOWN_PUBLICATION_MODE"):
        runtime._object_maintenance_activities()


def test_worker_object_maintenance_composes_s3_and_durable_inventory_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PODDOWN_OBJECT_MAINTENANCE_ENABLED", "1")
    monkeypatch.setenv("PODDOWN_PUBLICATION_MODE", "s3")
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    monkeypatch.setenv("PODDOWN_MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("PODDOWN_MINIO_BUCKET", "poddown")
    monkeypatch.setenv("PODDOWN_MINIO_SECRET_REF", "secret://minio/poddown")
    monkeypatch.setenv("PODDOWN_MINIO_ACCESS_KEY", "local-user")
    monkeypatch.setenv("PODDOWN_MINIO_SECRET_KEY", "local-password")
    monkeypatch.setenv("PODDOWN_MINIO_ALLOW_INSECURE_LOCAL", "1")
    initialized: list[object] = []
    monkeypatch.setattr(runtime, "Boto3S3Transport", lambda *_args: object())
    monkeypatch.setattr(
        runtime,
        "postgres_connection_factory",
        lambda _dsn: lambda: object(),
    )
    monkeypatch.setattr(runtime, "initialize_postgres", initialized.append)

    activities = runtime._object_maintenance_activities()

    assert len(activities) == 1
    assert callable(activities[0])
    assert len(initialized) == 1


def test_worker_outbox_relay_is_disabled_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PODDOWN_OUTBOX_RELAY_ENABLED", raising=False)

    assert runtime._outbox_relay_activities() == []


def test_worker_outbox_relay_requires_postgres_and_nats_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PODDOWN_OUTBOX_RELAY_ENABLED", "1")
    monkeypatch.delenv("PODDOWN_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("PODDOWN_NATS_HOST", raising=False)

    with pytest.raises(RuntimeConfigurationError, match="PODDOWN_POSTGRES_DSN"):
        runtime._outbox_relay_activities()


def test_worker_outbox_relay_composes_durable_ports_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PODDOWN_OUTBOX_RELAY_ENABLED", "1")
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    monkeypatch.setenv("PODDOWN_NATS_HOST", "nats:4222")
    sentinel_factory = object()
    initialized: list[object] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        runtime,
        "postgres_connection_factory",
        lambda _dsn: sentinel_factory,
    )
    monkeypatch.setattr(runtime, "initialize_postgres", initialized.append)

    def capture_builder(*args: object, **kwargs: object) -> object:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return lambda _payload: None

    monkeypatch.setattr(runtime, "build_nats_outbox_relay_activity", capture_builder)

    activities = runtime._outbox_relay_activities()

    assert len(activities) == 1
    assert callable(activities[0])
    assert initialized == [sentinel_factory]
    assert captured["kwargs"] == {"subject_prefix": "poddown.events"}


def test_live_provider_usage_requires_postgres_durable_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PODDOWN_POSTGRES_DSN", raising=False)

    with pytest.raises(RuntimeConfigurationError, match="PODDOWN_POSTGRES_DSN"):
        runtime._provider_usage_ledger(tmp_path, require_postgres=True)


def test_provider_usage_ledger_selects_postgres_for_configured_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sentinel_factory = object()
    sentinel_ledger = object()
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    monkeypatch.setattr(
        runtime,
        "postgres_connection_factory",
        lambda _dsn: sentinel_factory,
    )
    monkeypatch.setattr(
        runtime,
        "PostgresUsageLedger",
        lambda factory: (sentinel_ledger, factory)[0],
    )

    assert (
        runtime._provider_usage_ledger(tmp_path, require_postgres=True)
        is sentinel_ledger
    )


def test_provider_usage_ledger_keeps_sqlite_for_explicit_local_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PODDOWN_POSTGRES_DSN", raising=False)

    ledger = runtime._provider_usage_ledger(tmp_path, require_postgres=False)

    assert isinstance(ledger, SQLiteUsageLedger)


def test_worker_publication_target_registry_accepts_only_secret_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PODDOWN_PUBLICATION_TARGETS_JSON",
        json.dumps(
            [
                {
                    "tenant_id": "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10",
                    "project_id": "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12",
                    "target_id": "filesystem-target",
                    "kind": "filesystem",
                    "secret_ref": "secret://publication/filesystem",
                    "show_id": "show-1",
                    "feed_url": "https://example.test/feed.xml",
                }
            ]
        ),
    )

    resolver = runtime._publication_target_resolver()
    target = resolver(
        UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"),
        UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"),
        "filesystem-target",
    )

    assert target is not None
    assert target.secret_ref == "secret://publication/filesystem"
    assert (
        resolver(
            UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"),
            UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"),
            "other-target",
        )
        is None
    )


def test_api_runtime_settings_accepts_explicit_postgres_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PODDOWN_SQLITE_PATH", raising=False)
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")

    settings = api_runtime_settings()

    assert settings.database_path is None
    assert settings.postgres_dsn == "postgresql://poddown@postgres/poddown"


def test_api_runtime_settings_rejects_ambiguous_database_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PODDOWN_SQLITE_PATH", str(tmp_path / "poddown.sqlite3"))
    monkeypatch.setenv("PODDOWN_POSTGRES_DSN", "postgresql://poddown@postgres/poddown")
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")

    with pytest.raises(RuntimeConfigurationError, match="exactly one"):
        api_runtime_settings()


def test_api_composition_wires_postgres_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_create_app(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(runtime, "create_app", fake_create_app)
    monkeypatch.setattr(
        runtime,
        "postgres_connection_factory",
        lambda _dsn: lambda: object(),
    )
    monkeypatch.setattr(runtime, "initialize_postgres", lambda _factory: ())
    settings = ApiRuntimeSettings(
        database_path=None,
        temporal_address="temporal:7233",
        temporal_namespace="default",
        task_queue="poddown-default",
        postgres_dsn="postgresql://poddown@postgres/poddown",
    )

    assert build_api_app(settings) is not None
    assert captured["episode_repository"].__class__.__name__ == (
        "PostgresEpisodeRepository"
    )
    assert captured["command_receipt_store"].__class__.__name__ == (
        "PostgresCommandReceiptStore"
    )
    assert captured["approval_repository"].__class__.__name__ == (
        "PostgresApprovalRepository"
    )
    assert captured["require_workflow_snapshot"] is True


def test_worker_readiness_marker_tracks_started_worker_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "worker.ready"
    monkeypatch.setenv("PODDOWN_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("PODDOWN_TEMPORAL_TASK_QUEUE", "poddown-default")
    monkeypatch.setenv("PODDOWN_WORKER_READY_FILE", str(marker))
    monkeypatch.setenv("PODDOWN_RENDER_DATA_DIR", str(tmp_path / "render-data"))

    class FakeClient:
        @classmethod
        async def connect(cls, address: str, namespace: str) -> object:
            return object()

    class FakeWorker:
        is_running = False
        registered_activities: tuple[object, ...] = ()
        registered_workflows: tuple[object, ...] = ()

        def __init__(self, *_args, **kwargs) -> None:
            self.is_running = False
            type(self).registered_activities = tuple(kwargs["activities"])
            type(self).registered_workflows = tuple(kwargs["workflows"])

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
    # The worker now registers the complete production stage surface in
    # addition to the durable render and publication handlers.
    assert len(FakeWorker.registered_activities) == 8
    assert OutboxRelayWorkflow in FakeWorker.registered_workflows

    async def activity_requires_the_durable_workflow_payload() -> None:
        with pytest.raises(ApplicationError) as error:
            await FakeWorker.registered_activities[0]({})  # type: ignore[operator]
        assert getattr(error.value, "type", None) == "WorkflowContractError"

    asyncio.run(activity_requires_the_durable_workflow_payload())

    async def publication_activity_requires_a_complete_request() -> None:
        with pytest.raises(ApplicationError) as error:
            await FakeWorker.registered_activities[1]({})  # type: ignore[operator]
        assert getattr(error.value, "type", None) == "PublishingValidationError"

    asyncio.run(publication_activity_requires_a_complete_request())
