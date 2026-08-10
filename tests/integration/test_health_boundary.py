"""Offline ASGI health boundary tests."""

from fastapi.testclient import TestClient

from poddown.api import create_app
from poddown.production_readiness import DependencyState


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
    assert ready.json()["status"] == "degraded"
    assert ready.json()["dependencies"]["postgres"] == "unavailable"
