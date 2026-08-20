"""BDD bindings for the offline production-readiness contract."""

from __future__ import annotations

import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml
from pytest_bdd import given, scenarios, then, when

from poddown.production_readiness import (
    DependencyState,
    HealthEvaluator,
    OperationalEvent,
)

scenarios("../features/production_readiness.feature")

TENANT = "tenant-018f"
PROJECT = "project-0190"


@given("an offline production-readiness evaluator")
def evaluator(context) -> None:
    context.values["evaluator"] = HealthEvaluator()


@when("PostgreSQL is unavailable but the process is alive")
def postgres_unavailable(context) -> None:
    context.values["health"] = context.values["evaluator"].evaluate(
        liveness=True,
        dependencies={
            "postgres": DependencyState.UNAVAILABLE,
            "nats": DependencyState.HEALTHY,
        },
    )


@then("liveness is healthy and readiness is degraded")
def degraded_health(context) -> None:
    health = context.values["health"]
    assert health.liveness == "healthy"
    assert health.readiness == "degraded"
    assert health.dependencies["postgres"] == DependencyState.UNAVAILABLE


@given("an offline operational event with source audio and credential fields")
def unsafe_event(context) -> None:
    context.values["event"] = OperationalEvent.create(
        event_name="episode.failed",
        tenant_id=TENANT,
        project_id=PROJECT,
        correlation_id="corr-1",
        attributes={
            "source_text": "PRIVATE SOURCE",
            "audio_bytes": b"audio",
            "credential": "secret-value",
            "safe_stage": "qa",
        },
    )


@when("the event is serialized")
def serialize_event(context) -> None:
    context.values["serialized"] = context.values["event"].to_dict()


@then("the event contains tenant-safe metadata and no unsafe values")
def safe_event(context) -> None:
    serialized = context.values["serialized"]
    assert serialized["tenant_id"] == TENANT
    assert serialized["attributes"] == {
        "audio_bytes": "[REDACTED]",
        "credential": "[REDACTED]",
        "safe_stage": "qa",
        "source_text": "[REDACTED]",
    }
    assert "PRIVATE SOURCE" not in str(serialized)
    assert "secret-value" not in str(serialized)


@given("the versioned local Compose contract")
def compose_contract(context) -> None:
    context.values["compose"] = yaml.safe_load(
        (Path(__file__).parents[2] / "compose.yaml").read_text()
    )


@when("the Compose YAML is parsed")
def parse_compose(context) -> None:
    context.values["parsed"] = context.values["compose"]


@then(
    "it contains the six required runtime services, the MinIO bootstrap helper, "
    "and health-gated dependencies"
)
def compose_services(context) -> None:
    services = context.values["parsed"]["services"]
    assert set(services) == {
        "postgres",
        "temporal",
        "nats",
        "minio",
        "minio-init",
        "api",
        "worker",
    }
    assert services["minio-init"]["depends_on"]["minio"]["condition"] == (
        "service_healthy"
    )
    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert (
        services["worker"]["depends_on"]["temporal"]["condition"] == "service_healthy"
    )


@when("the worker workflow environment is inspected")
def inspect_worker_workflow_environment(context) -> None:
    context.values["worker_environment"] = context.values["compose"]["services"][
        "worker"
    ]["environment"]


@then("the worker receives the host-local reference fixture and mode")
def worker_receives_reference_fixture(context) -> None:
    environment = context.values["worker_environment"]
    assert environment["PODDOWN_WORKFLOW_FIXTURE_ROOT"] == (
        "/app/integrations/reference-demo/v1"
    )
    assert environment["PODDOWN_WORKFLOW_MODE"] == "host-local"


@when("the API readiness probe is prepared from the Python image contract")
def prepare_api_readiness_probe(context) -> None:
    healthcheck = context.values["compose"]["services"]["api"]["healthcheck"]["test"]
    assert healthcheck[:3] == ["CMD", "python", "-c"]
    assert (
        (Path(__file__).parents[2] / "Dockerfile")
        .read_text()
        .startswith("FROM python:3.13-slim")
    )
    context.values["probe"] = healthcheck[3]


@then("the API readiness probe succeeds against a local ready endpoint")
def execute_api_readiness_probe(context) -> None:
    class ReadyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ReadyHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        probe = context.values["probe"].replace(
            "http://localhost:8000", f"http://127.0.0.1:{server.server_port}"
        )
        context.values["probe_result"] = subprocess.run(
            [sys.executable, "-c", probe], check=False, capture_output=True
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert context.values["probe_result"].returncode == 0


@given("an unsupported mutable operational event attribute")
def unsupported_event_attribute(context) -> None:
    context.values["attributes"] = {"tags": {"production", "ready"}}


@when("operational event creation is attempted")
def create_unsupported_event(context) -> None:
    with pytest.raises(ValueError, match="JSON-safe") as error:
        OperationalEvent.create(
            event_name="episode.qa",
            tenant_id=TENANT,
            project_id=PROJECT,
            correlation_id="corr-1",
            attributes=context.values["attributes"],
        )
    context.values["error"] = error.value


@then("the unsupported event value is rejected before it can mutate or serialize")
def unsupported_event_is_rejected(context) -> None:
    context.values["attributes"]["tags"].add("mutated")
    assert "JSON-safe" in str(context.values["error"])
