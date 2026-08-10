"""Offline ASGI health boundary tests."""

from fastapi.testclient import TestClient

from poddown.api import create_app
from poddown.production_readiness import DependencyState
from poddown.runtime import runtime_dependency_probes


def test_health_routes_are_available_without_services_or_credentials() -> None:
    with TestClient(create_app()) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        dependencies = client.get("/health/dependencies")
    assert live.status_code == 200
    assert live.json()["status"] == "healthy"
    assert ready.status_code == 200
    assert ready.json()["status"] == "healthy"
    assert dependencies.status_code == 200
    assert dependencies.json()["dependencies"] == {}


def test_health_readiness_reports_injected_dependency_degradation() -> None:
    with TestClient(
        create_app(health_dependencies={"postgres": DependencyState.UNAVAILABLE})
    ) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
    assert live.json() == {"status": "healthy"}
    assert live.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["status"] == "degraded"
    assert ready.json()["dependencies"]["postgres"] == "unavailable"


def test_health_probe_is_evaluated_on_each_request_and_exceptions_degrade() -> None:
    available = True

    def probe() -> bool:
        if not available:
            raise RuntimeError("database unavailable")
        return True

    with TestClient(create_app(health_probes={"postgres": probe})) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/health/ready").json()["status"] == "healthy"
        available = False
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"]["postgres"] == "unavailable"


def test_runtime_probe_configuration_is_fail_closed_when_absent(monkeypatch) -> None:
    for name in (
        "PODDOWN_POSTGRES_HOST",
        "PODDOWN_TEMPORAL_ADDRESS",
        "PODDOWN_NATS_HOST",
        "PODDOWN_MINIO_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    probes = runtime_dependency_probes()
    assert set(probes) == {"postgres", "temporal", "nats", "minio"}
    assert all(probe() is False for probe in probes.values())
