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
    assert set(document["services"]) == REQUIRED
    assert all(
        ":" in service["image"]
        for service in document["services"].values()
        if "image" in service
    )
    assert not list(COMPOSE.parent.glob("*.kube.yaml"))


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
        "PODDOWN_TEMPORAL_ADDRESS": "temporal:7233",
        "PODDOWN_NATS_HOST": "nats",
        "PODDOWN_MINIO_ENDPOINT": "minio:9000",
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
