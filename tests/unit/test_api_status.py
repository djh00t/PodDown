"""Tests for API status projection from the durable Temporal workflow."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from poddown.api import create_app
from poddown.api.temporal_dispatcher import TemporalWorkflowStatus

TENANT = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
SOURCE = "---\npoddown:\n  profile: technical-dialogue\n---\n# Status fixture\n"


class _CompletedTemporalTransport:
    """Return one safe production result after accepting the API command."""

    def __init__(self, result: str) -> None:
        self.result = result
        self.requests: list[Any] = []

    def start_workflow(self, request: Any) -> None:
        self.requests.append(request)

    def get_workflow_status(self, workflow_id: str) -> TemporalWorkflowStatus:
        assert workflow_id == self.requests[0].workflow_id
        return TemporalWorkflowStatus(state="completed", result=self.result)


def test_status_projects_completed_temporal_manifest_digest() -> None:
    """A completed production workflow must replace the initial ingested view."""
    package_sha256 = "a" * 64
    manifest_sha256 = "b" * 64
    transport = _CompletedTemporalTransport(
        "{" + f'"workflow_id":"episode-production-1","status":"completed",'
        f'"stage":"packaged","package_sha256":"{package_sha256}",'
        f'"package_manifest_sha256":"{manifest_sha256}","publication":null' + "}"
    )
    app = create_app(
        temporal_transport=transport,
        temporal_task_queue="poddown-test",
        available_profiles={"technical-dialogue"},
    )

    with TestClient(app) as client:
        created = client.post(
            "/v1/episodes",
            headers={
                "X-Tenant-ID": TENANT,
                "X-Project-ID": PROJECT,
                "Idempotency-Key": "status-temporal-001",
            },
            json={"source": SOURCE, "profile": "technical-dialogue"},
        )
        assert created.status_code == 202
        episode_id = created.json()["episode"]["id"]

        response = client.get(
            f"/v1/episodes/{episode_id}/status",
            headers={"X-Tenant-ID": TENANT, "X-Project-ID": PROJECT},
        )

    assert response.status_code == 200
    assert response.json()["stage"] == "packaged"
    assert response.json()["progress"] == 0.9
    assert response.json()["package_manifest_sha256"] == manifest_sha256
    assert response.json()["workflow_id"] == transport.requests[0].workflow_id
