"""Unit contracts for deterministic health and telemetry redaction."""

from dataclasses import FrozenInstanceError

import pytest

from poddown.production_readiness import (
    DependencyState,
    HealthEvaluator,
    MetricSample,
    OperationalEvent,
)


def test_health_evaluator_is_deterministic_and_distinguishes_liveness() -> None:
    evaluator = HealthEvaluator()
    first = evaluator.evaluate(
        liveness=True,
        dependencies={
            "nats": DependencyState.HEALTHY,
            "postgres": DependencyState.DEGRADED,
        },
    )
    second = evaluator.evaluate(
        liveness=True,
        dependencies={
            "postgres": DependencyState.DEGRADED,
            "nats": DependencyState.HEALTHY,
        },
    )
    assert first == second
    assert first.liveness == "healthy"
    assert first.readiness == "degraded"


def test_health_is_not_ready_when_process_is_not_alive() -> None:
    snapshot = HealthEvaluator().evaluate(liveness=False, dependencies={})
    assert snapshot.liveness == "unhealthy"
    assert snapshot.readiness == "unhealthy"


def test_operational_event_redacts_sensitive_keys_and_is_immutable() -> None:
    event = OperationalEvent.create(
        event_name="render.failed",
        tenant_id="tenant-1",
        project_id="project-1",
        correlation_id="corr-1",
        attributes={
            "source": "private",
            "script": "private",
            "audio": "private",
            "api_key": "private",
            "stage": "render",
        },
    )
    assert event.to_dict()["attributes"] == {
        "api_key": "[REDACTED]",
        "audio": "[REDACTED]",
        "script": "[REDACTED]",
        "source": "[REDACTED]",
        "stage": "render",
    }
    with pytest.raises(FrozenInstanceError):
        event.event_name = "changed"  # type: ignore[misc]


def test_metric_rejects_raw_sensitive_values() -> None:
    with pytest.raises(ValueError, match="redacted"):
        MetricSample.create(
            name="episode.render.seconds",
            value=1.0,
            tenant_id="tenant-1",
            project_id="project-1",
            labels={"source_text": "private"},
        )
