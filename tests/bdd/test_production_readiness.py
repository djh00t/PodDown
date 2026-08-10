"""BDD bindings for the offline production-readiness contract."""

from __future__ import annotations

from pathlib import Path

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


@then("it contains the six required runtime services and health-gated dependencies")
def compose_services(context) -> None:
    services = context.values["parsed"]["services"]
    assert set(services) == {"postgres", "temporal", "nats", "minio", "api", "worker"}
    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert (
        services["worker"]["depends_on"]["temporal"]["condition"] == "service_healthy"
    )
