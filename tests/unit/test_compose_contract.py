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


def test_compose_contract_declares_runtime_build_context_and_entrypoints() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    assert services["api"]["build"] == "."
    assert services["worker"]["build"] == "."
    assert services["api"]["command"] == ["poddown-api"]
    assert services["worker"]["command"] == ["poddown-worker"]


def test_api_declares_runtime_dependency_probe_endpoints_and_ready_healthcheck() -> (
    None
):
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    assert services["api"]["environment"] == {
        "PODDOWN_POSTGRES_HOST": "postgres",
        "PODDOWN_TEMPORAL_ADDRESS": "temporal:7233",
        "PODDOWN_NATS_HOST": "nats",
        "PODDOWN_MINIO_ENDPOINT": "minio:9000",
    }
    assert services["api"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        "curl -fsS http://localhost:8000/health/ready || exit 1",
    ]
