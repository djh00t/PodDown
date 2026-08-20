"""Static validation for the versioned local Compose contract."""

import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import yaml

COMPOSE = Path(__file__).parents[2] / "compose.yaml"
DOCKERFILE = COMPOSE.parent / "Dockerfile"
REQUIRED = {"postgres", "temporal", "nats", "minio", "api", "worker"}


def test_compose_contract_has_required_services_and_no_kubernetes() -> None:
    document = yaml.safe_load(COMPOSE.read_text())
    assert document["x-poddown"]["contract_version"] == "m7.v1"
    assert set(document["services"]) == REQUIRED | {"minio-init"}
    assert all(
        ":" in service["image"]
        for service in document["services"].values()
        if "image" in service
    )
    assert not list(COMPOSE.parent.glob("*.kube.yaml"))


def test_compose_bootstraps_the_explicit_minio_publication_bucket() -> None:
    """Application services must wait for the local MinIO bucket bootstrap."""
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    bootstrap = services["minio-init"]
    assert bootstrap["image"] == "minio/mc:RELEASE.2025-08-13T08-35-41Z"
    assert bootstrap["entrypoint"] == ["/bin/sh", "-c"]
    assert "mc mb --ignore-existing local/poddown" in bootstrap["command"][0]
    assert bootstrap["depends_on"]["minio"]["condition"] == "service_healthy"
    for service in (services["api"], services["worker"]):
        assert service["depends_on"]["minio-init"]["condition"] == (
            "service_completed_successfully"
        )


def test_temporal_auto_setup_uses_the_supported_postgres_driver() -> None:
    """Temporal 1.25 names its PostgreSQL driver postgres12."""
    temporal = yaml.safe_load(COMPOSE.read_text())["services"]["temporal"]
    assert temporal["environment"]["DB"] == "postgres12"
    assert temporal["environment"]["POSTGRES_SEEDS"] == "postgres"
    assert temporal["environment"]["TEMPORAL_ADDRESS"] == "temporal:7233"
    assert temporal["healthcheck"]["test"] == [
        "CMD",
        "temporal",
        "operator",
        "cluster",
        "health",
        "--address",
        "temporal:7233",
        "--output",
        "json",
    ]


def test_compose_contract_has_healthchecks_and_dependency_gates() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    for name in REQUIRED:
        assert "healthcheck" in services[name]
    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["temporal"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["nats"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["minio"]["condition"] == "service_healthy"
    assert (
        services["worker"]["depends_on"]["temporal"]["condition"] == "service_healthy"
    )


def test_compose_contract_declares_runtime_build_context_and_entrypoints() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    assert services["api"]["build"] == "."
    assert services["worker"]["build"] == "."
    assert services["api"]["command"] == ["poddown-api"]
    assert services["worker"]["command"] == ["poddown-worker"]


def test_compose_runtime_selects_postgres_and_shared_render_storage() -> None:
    """The local stack must not advertise Postgres while booting SQLite."""
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    api_environment = services["api"]["environment"]
    worker_environment = services["worker"]["environment"]

    assert api_environment["PODDOWN_POSTGRES_DSN"] == (
        "postgresql://poddown@postgres/poddown"
    )
    assert "PODDOWN_SQLITE_PATH" not in api_environment
    assert api_environment["PODDOWN_PUBLICATION_MODE"] == "s3"
    assert worker_environment["PODDOWN_POSTGRES_DSN"] == (
        "postgresql://poddown@postgres/poddown"
    )
    assert worker_environment["PODDOWN_NATS_HOST"] == "nats:4222"
    assert worker_environment["PODDOWN_PUBLICATION_MODE"] == "s3"
    assert worker_environment["PODDOWN_OUTBOX_RELAY_ENABLED"] == "1"
    assert worker_environment["PODDOWN_RENDER_DATA_DIR"] == "/data/render"
    assert worker_environment["PODDOWN_WORKFLOW_FIXTURE_ROOT"] == (
        "/app/integrations/reference-demo/v1"
    )
    assert worker_environment["PODDOWN_WORKFLOW_MODE"] == "host-local"
    assert services["worker"]["volumes"] == ["render-data:/data/render"]


def test_api_ready_healthcheck_executes_with_the_built_image_python() -> None:
    """The image-provided Python can run the Compose readiness probe without curl."""

    class ReadyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    services = yaml.safe_load(COMPOSE.read_text())["services"]
    assert services["api"]["environment"] == {
        "PODDOWN_POSTGRES_HOST": "postgres",
        "PODDOWN_POSTGRES_DSN": "postgresql://poddown@postgres/poddown",
        "PGPASSWORD": "${PODDOWN_POSTGRES_PASSWORD:?set a local-only password}",
        "PODDOWN_TEMPORAL_ADDRESS": "temporal:7233",
        "PODDOWN_TEMPORAL_NAMESPACE": "default",
        "PODDOWN_TEMPORAL_TASK_QUEUE": "poddown-default",
        "PODDOWN_AUTH_MODE": "local",
        "PODDOWN_WORKFLOW_FIXTURE_ROOT": "/app/integrations/reference-demo/v1",
        "PODDOWN_WORKFLOW_MODE": "host-local",
        "PODDOWN_PUBLICATION_MODE": "s3",
        "PODDOWN_NATS_HOST": "nats",
        "PODDOWN_MINIO_ENDPOINT": "http://minio:9000",
        "PODDOWN_MINIO_BUCKET": "poddown",
        "PODDOWN_MINIO_SECRET_REF": "secret://minio/poddown",
        "PODDOWN_MINIO_ACCESS_KEY": "${PODDOWN_MINIO_ROOT_USER:?set a local-only user}",
        "PODDOWN_MINIO_SECRET_KEY": (
            "${PODDOWN_MINIO_ROOT_PASSWORD:?set a local-only password}"
        ),
        "PODDOWN_MINIO_ALLOW_INSECURE_LOCAL": "1",
        "PODDOWN_RESOURCE_LINK_BASE_URL": (
            "${PODDOWN_RESOURCE_LINK_BASE_URL:?set an HTTPS resource URL}"
        ),
        "PODDOWN_RESOURCE_LINK_SECRET": (
            "${PODDOWN_RESOURCE_LINK_SECRET:?set a resource-link secret}"
        ),
        "PODDOWN_RESOURCE_LINK_TTL_SECONDS": (
            "${PODDOWN_RESOURCE_LINK_TTL_SECONDS:-300}"
        ),
    }
    assert DOCKERFILE.read_text().startswith("FROM python:3.13-slim")
    healthcheck = services["api"]["healthcheck"]["test"]
    assert healthcheck[:3] == ["CMD", "python", "-c"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), ReadyHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        probe = healthcheck[3].replace(
            "http://localhost:8000", f"http://127.0.0.1:{server.server_port}"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe], check=False, capture_output=True
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert completed.returncode == 0, completed.stderr.decode()
