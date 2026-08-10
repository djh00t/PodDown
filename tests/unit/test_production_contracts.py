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


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_metric_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        MetricSample.create(
            name="episode.render.seconds",
            value=value,
            tenant_id="tenant-1",
            project_id="project-1",
            labels={"stage": "render"},
        )


def test_metric_rejects_unsafe_or_oversized_labels_but_accepts_small_safe_labels() -> (
    None
):
    metric = MetricSample.create(
        name="episode.render.seconds",
        value=1.5,
        tenant_id="tenant-1",
        project_id="project-1",
        labels={"stage": "render", "attempt": "1"},
    )
    assert metric.labels["stage"] == "render"
    with pytest.raises(ValueError, match="label"):
        MetricSample.create(
            name="episode.render.seconds",
            value=1,
            tenant_id="tenant-1",
            project_id="project-1",
            labels={"stage": "raw provider payload"},
        )
    with pytest.raises(ValueError, match="label"):
        MetricSample.create(
            name="episode.render.seconds",
            value=1,
            tenant_id="tenant-1",
            project_id="project-1",
            labels={"stage": "x" * 129},
        )


def test_operational_event_redacts_nested_sequences_and_key_variants() -> None:
    event = OperationalEvent.create(
        event_name="episode.completed",
        tenant_id="tenant-1",
        project_id="project-1",
        correlation_id="corr-1",
        attributes={
            "safe": [
                {"voice_id": "voice-secret", "path": "/private/audio.mp3"},
                {"uri": "https://private.example/audio"},
            ],
            "transcript": "private transcript",
            "providerPayload": {"request": "private"},
        },
    )
    assert event.to_dict()["attributes"] == {
        "providerPayload": "[REDACTED]",
        "safe": [
            {"path": "[REDACTED]", "voice_id": "[REDACTED]"},
            {"uri": "[REDACTED]"},
        ],
        "transcript": "[REDACTED]",
    }


def test_operational_event_deep_freezes_input_and_safe_serialization() -> None:
    nested = {"safe": [{"stage": "qa"}]}
    event = OperationalEvent.create(
        event_name="episode.qa",
        tenant_id="tenant-1",
        project_id="project-1",
        correlation_id="corr-1",
        attributes=nested,
    )
    nested["safe"][0]["stage"] = "changed"
    serialized = event.to_dict()
    serialized["attributes"]["safe"][0]["stage"] = "mutated-output"
    assert event.to_dict()["attributes"]["safe"][0]["stage"] == "qa"
    with pytest.raises(TypeError):
        event.attributes["safe"][0]["stage"] = "blocked"  # type: ignore[index]
