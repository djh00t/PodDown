"""Static validation for the versioned local Compose contract."""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).parents[2] / "compose.yaml"
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
