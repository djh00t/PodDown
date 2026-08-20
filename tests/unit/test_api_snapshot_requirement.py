"""API fail-closed behavior when a Temporal snapshot factory is absent."""

from __future__ import annotations

from fastapi.testclient import TestClient

from poddown.api import create_app

TENANT = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"


class _Transport:
    def __init__(self) -> None:
        self.started = False

    def start_workflow(self, request) -> None:
        del request
        self.started = True


def test_temporal_api_requires_a_workflow_snapshot_factory() -> None:
    transport = _Transport()
    with TestClient(
        create_app(
            temporal_transport=transport,
            temporal_task_queue="poddown-default",
            require_workflow_snapshot=True,
        )
    ) as client:
        response = client.post(
            "/v1/episodes",
            headers={
                "X-Tenant-ID": TENANT,
                "X-Project-ID": PROJECT,
                "Idempotency-Key": "missing-snapshot-factory",
            },
            json={
                "source": (
                    "---\npoddown:\n  profile: technical-dialogue\n---\n# Source\n"
                ),
                "profile": "technical-dialogue",
            },
        )

    assert response.status_code == 503
    assert response.json()["code"] == "workflow_snapshot_unavailable"
    assert transport.started is False
