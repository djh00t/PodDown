"""Offline ASGI contract tests for all five Spec 004 episode routes.

The planned app factory must provide deterministic in-memory test wiring. These
tests intentionally do not configure databases, object storage, credentials, or
provider/network clients.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from uuid6 import uuid7

from poddown.api import create_app
from poddown.api.temporal_dispatcher import TemporalCommandRequest
from poddown.approvals import PublicationApproval, SQLiteApprovalRepository
from poddown.episode_service import (
    EpisodeApplicationService,
    EpisodeCreateCommand,
    EpisodeState,
    InMemoryEpisodeRepository,
    StructuredFailure,
)

TENANT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
OTHER_TENANT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"
PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
OTHER_PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"
PROFILE = "technical-dialogue"
SOURCE = "---\npoddown:\n  profile: technical-dialogue\n---\n# Offline API fixture\n"
UUIDV7 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@pytest.fixture
def client() -> TestClient:
    """Use the API's required offline/demo composition; no external adapters."""
    with TestClient(create_app()) as test_client:
        yield test_client


def _headers(
    *,
    tenant_id: str = TENANT_ID,
    project_id: str = PROJECT_ID,
    key: str = "episode-create-001",
) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "X-Project-ID": project_id,
        "Idempotency-Key": key,
    }


def _assert_problem(response, *, status: int, code: str | None = None) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"type", "title", "status", "code", "detail"}
    assert body["status"] == status
    if code is not None:
        assert body["code"] == code
    assert SOURCE not in str(body)
    assert "API_KEY" not in str(body)


def _create_episode(client: TestClient) -> dict:
    response = client.post(
        "/v1/episodes",
        headers=_headers(),
        json={"source": SOURCE, "profile": PROFILE},
    )
    assert response.status_code == 202
    return response.json()


def test_create_can_use_explicit_temporal_dispatcher_transport() -> None:
    """Production composition must expose workflow dispatch only when injected."""

    class Transport:
        def __init__(self) -> None:
            self.requests: list[TemporalCommandRequest] = []

        def start_workflow(self, request: TemporalCommandRequest) -> None:
            self.requests.append(request)

    transport = Transport()
    with TestClient(
        create_app(
            temporal_transport=transport,
            temporal_task_queue="poddown-default",
        )
    ) as client:
        response = client.post(
            "/v1/episodes",
            headers=_headers(key="temporal-create-001"),
            json={"source": SOURCE, "profile": PROFILE},
        )

    assert response.status_code == 202
    assert response.json()["receipt"]["state"] == "dispatched"
    assert (
        response.json()["receipt"]["workflow_id"] == transport.requests[0].workflow_id
    )


def test_render_wire_controls_are_preserved_in_temporal_command_payload() -> None:
    class Transport:
        def __init__(self) -> None:
            self.requests: list[TemporalCommandRequest] = []

        def start_workflow(self, request: TemporalCommandRequest) -> None:
            self.requests.append(request)

    transport = Transport()
    with TestClient(
        create_app(
            temporal_transport=transport,
            temporal_task_queue="poddown-default",
        )
    ) as client:
        episode = _create_episode(client)
        response = client.post(
            f"/v1/episodes/{episode['episode']['id']}/render",
            headers=_headers(key="render-wire-001"),
            json={
                "provider_route_id": "route-local-v1",
                "mode": "deterministic-local",
                "max_cost": "1.00",
            },
        )

    assert response.status_code == 202
    assert json.loads(transport.requests[-1].payload)["payload"] == {
        "max_cost": "1.00",
        "mode": "deterministic-local",
        "provider_route_id": "route-local-v1",
    }


def test_publish_wire_payload_carries_only_compact_worker_references(
    tmp_path: Path,
) -> None:
    """API publication dispatch must not serialize package bytes or target secrets."""

    class Transport:
        def __init__(self) -> None:
            self.requests: list[TemporalCommandRequest] = []

        def start_workflow(self, request: TemporalCommandRequest) -> None:
            self.requests.append(request)

    repository = InMemoryEpisodeRepository()
    service = EpisodeApplicationService(
        repository=repository,
        available_profiles={PROFILE},
    )
    created = service.create_episode(
        EpisodeCreateCommand(
            tenant_id=UUID(TENANT_ID),
            project_id=UUID(PROJECT_ID),
            idempotency_key="publish-wire-create-001",
            source_bytes=SOURCE.encode("utf-8"),
            profile_name=PROFILE,
        )
    )
    scripted = service.transition(
        UUID(TENANT_ID),
        created.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=created.version,
    )
    rendered = service.transition(
        UUID(TENANT_ID),
        created.episode_id,
        EpisodeState.RENDERED,
        expected_version=scripted.version,
    )
    qa_passed = service.transition(
        UUID(TENANT_ID),
        created.episode_id,
        EpisodeState.QA_PASSED,
        expected_version=rendered.version,
        qa_evidence={"status": "pass"},
    )
    package_bytes = b"immutable package bytes"
    package_sha256 = sha256(package_bytes).hexdigest()
    manifest_sha256 = "c" * 64
    packaged = service.transition(
        UUID(TENANT_ID),
        created.episode_id,
        EpisodeState.PACKAGED,
        expected_version=qa_passed.version,
        package_sha256=package_sha256,
        package_bytes=package_bytes,
        package_manifest_sha256=manifest_sha256,
    )
    approval_repository = SQLiteApprovalRepository(tmp_path / "approvals.sqlite3")
    approval_id = uuid7()
    approval_repository.issue(
        PublicationApproval(
            tenant_id=UUID(TENANT_ID),
            project_id=UUID(PROJECT_ID),
            approval_id=approval_id,
            episode_id=packaged.episode_id,
            target_id="filesystem-target",
            operation="publish",
            package_sha256=package_sha256,
            actor_id="local-header",
            nonce_sha256="d" * 64,
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    transport = Transport()
    with TestClient(
        create_app(
            service,
            temporal_transport=transport,
            temporal_task_queue="poddown-default",
            approval_repository=approval_repository,
        )
    ) as client:
        response = client.post(
            f"/v1/episodes/{packaged.episode_id}/publish",
            headers=_headers(key="fixture-publish"),
            json={"target_id": "filesystem-target", "approval_id": str(approval_id)},
        )

    assert response.status_code == 202
    payload = json.loads(transport.requests[-1].payload)["payload"]
    assert payload["target_id"] == "filesystem-target"
    assert payload["approval_id"] == str(approval_id)
    assert payload["package_reference"] == {
        "episode_version_id": str(packaged.episode_id),
        "package_sha256": package_sha256,
        "package_manifest_sha256": manifest_sha256,
    }
    assert payload["authorization"] == {
        "actor_id": "local-header",
        "decision_id": str(approval_id),
        "reason": "scoped publication approval",
        "operation": "publish",
    }
    assert "package" not in payload
    assert "target" not in payload


def test_status_projects_temporal_workflow_identity_from_create_receipt() -> None:
    """Status must expose the durable workflow identity when Temporal is configured."""

    class Transport:
        def __init__(self) -> None:
            self.requests: list[TemporalCommandRequest] = []

        def start_workflow(self, request: TemporalCommandRequest) -> None:
            self.requests.append(request)

    transport = Transport()
    with TestClient(
        create_app(
            temporal_transport=transport,
            temporal_task_queue="poddown-default",
        )
    ) as client:
        created = client.post(
            "/v1/episodes",
            headers=_headers(key="temporal-status-001"),
            json={"source": SOURCE, "profile": PROFILE},
        ).json()
        episode_id = created["episode"]["id"]
        status = client.get(
            f"/v1/episodes/{episode_id}/status",
            headers=_headers(key="temporal-status-read-001"),
        )

    assert status.status_code == 200
    assert status.json()["workflow_id"] == transport.requests[0].workflow_id


def test_post_episodes_returns_uuidv7_summary_and_create_receipt(
    client: TestClient,
) -> None:
    body = _create_episode(client)

    assert set(body) == {"episode", "receipt"}
    assert UUIDV7.fullmatch(body["episode"]["id"])
    assert body["episode"]["tenant_id"] == TENANT_ID
    assert body["episode"]["project_id"] == PROJECT_ID
    assert body["receipt"]["episode_id"] == body["episode"]["id"]
    assert body["receipt"]["idempotency_key"] == "episode-create-001"
    assert body["receipt"]["command"] == "create"
    assert body["receipt"]["accepted"] is True


def test_post_episodes_replays_exact_request_with_identical_summary_and_receipt(
    client: TestClient,
) -> None:
    first = _create_episode(client)
    second = _create_episode(client)

    assert second == first


def test_post_episodes_rejects_conflicting_idempotency_reuse(
    client: TestClient,
) -> None:
    _create_episode(client)
    response = client.post(
        "/v1/episodes",
        headers=_headers(),
        json={"source": SOURCE + "Changed", "profile": PROFILE},
    )

    _assert_problem(response, status=409, code="idempotency_conflict")


def test_create_idempotency_rejects_cross_project_key_rebinding(
    client: TestClient,
) -> None:
    _create_episode(client)
    response = client.post(
        "/v1/episodes",
        headers=_headers(project_id=OTHER_PROJECT_ID),
        json={"source": SOURCE, "profile": PROFILE},
    )

    _assert_problem(response, status=409, code="idempotency_conflict")


def test_post_episodes_rejects_missing_profile_with_stable_problem(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/episodes",
        headers=_headers(key="missing-profile-001"),
        json={"source": SOURCE},
    )

    _assert_problem(response, status=422, code="invalid_profile")


@pytest.mark.parametrize(
    ("headers", "content", "expected_status", "expected_code"),
    [
        (
            {"X-Project-ID": PROJECT_ID, "Idempotency-Key": "key"},
            None,
            400,
            "missing_tenant_id",
        ),
        (
            {
                "X-Tenant-ID": "bad",
                "X-Project-ID": PROJECT_ID,
                "Idempotency-Key": "key",
            },
            None,
            400,
            "invalid_tenant_id",
        ),
        (
            {"X-Tenant-ID": TENANT_ID, "Idempotency-Key": "key"},
            None,
            400,
            "missing_project_id",
        ),
        (
            {"X-Tenant-ID": TENANT_ID, "X-Project-ID": "bad", "Idempotency-Key": "key"},
            None,
            400,
            "invalid_project_id",
        ),
        (
            {"X-Tenant-ID": TENANT_ID, "X-Project-ID": PROJECT_ID},
            None,
            400,
            "missing_idempotency_key",
        ),
        (_headers(), None, 422, "invalid_profile"),
        (
            _headers(),
            b'{"source":"\xff","profile":"technical-dialogue"}',
            400,
            "invalid_source_encoding",
        ),
    ],
)
def test_post_episodes_returns_stable_redacted_problem_for_invalid_context(
    client: TestClient,
    headers: dict[str, str],
    content: bytes | None,
    expected_status: int,
    expected_code: str,
) -> None:
    response = client.post(
        "/v1/episodes",
        headers=headers,
        json=None
        if content is not None
        else {"source": SOURCE, "profile": "unknown-profile"},
        content=content,
    )

    _assert_problem(response, status=expected_status, code=expected_code)


def test_get_episode_and_status_return_tenant_scoped_summary_and_redacted_status(
    client: TestClient,
) -> None:
    episode_id = _create_episode(client)["episode"]["id"]

    episode = client.get(f"/v1/episodes/{episode_id}", headers=_headers(key="read-001"))
    status = client.get(
        f"/v1/episodes/{episode_id}/status", headers=_headers(key="status-001")
    )

    assert episode.status_code == 200
    assert episode.json()["id"] == episode_id
    assert status.status_code == 200
    assert set(status.json()) == {
        "episode_id",
        "episode_version_id",
        "version",
        "stage",
        "progress",
        "failure",
        "workflow_id",
        "package_manifest_sha256",
        "publication_id",
    }
    assert status.json()["version"] == episode.json()["version"]
    assert SOURCE not in str(status.json())
    assert "authorization" not in str(status.json()).lower()


def test_failed_status_projects_only_safe_failure_fields() -> None:
    repository = InMemoryEpisodeRepository()
    service = EpisodeApplicationService(
        repository=repository,
        available_profiles={PROFILE},
    )
    record = service.create_episode(
        EpisodeCreateCommand(
            tenant_id=UUID(TENANT_ID),
            project_id=UUID(PROJECT_ID),
            idempotency_key="failed-status-001",
            source_bytes=SOURCE.encode("utf-8"),
            profile_name=PROFILE,
        )
    )
    service.transition(
        UUID(TENANT_ID),
        record.episode_id,
        EpisodeState.FAILED,
        expected_version=record.version,
        failure=StructuredFailure(
            code="provider_failure",
            stage="render",
            message="provider failed for SECRET_SOURCE",
            retriable=False,
            details={
                "raw_source": "SECRET_SOURCE",
                "credential": "TOKEN",
                "parser_detail": "internal parser trace",
            },
            status=502,
        ),
    )

    with TestClient(create_app(service)) as failed_client:
        response = failed_client.get(
            f"/v1/episodes/{record.episode_id}/status",
            headers=_headers(key="failed-status-read-001"),
        )

    assert response.status_code == 200
    assert response.json()["failure"] == {
        "code": "provider_failure",
        "stage": "render",
        "status": 502,
        "retriable": False,
    }
    assert "SECRET_SOURCE" not in str(response.json())
    assert "TOKEN" not in str(response.json())
    assert "parser trace" not in str(response.json())


def test_status_returns_the_manifest_digest_separately_from_package_bytes_digest() -> (
    None
):
    repository = InMemoryEpisodeRepository()
    service = EpisodeApplicationService(
        repository=repository,
        available_profiles={PROFILE},
    )
    record = service.create_episode(
        EpisodeCreateCommand(
            tenant_id=UUID(TENANT_ID),
            project_id=UUID(PROJECT_ID),
            idempotency_key="manifest-status-001",
            source_bytes=SOURCE.encode("utf-8"),
            profile_name=PROFILE,
        )
    )
    scripted = service.transition(
        UUID(TENANT_ID),
        record.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=record.version,
    )
    rendered = service.transition(
        UUID(TENANT_ID),
        record.episode_id,
        EpisodeState.RENDERED,
        expected_version=scripted.version,
    )
    qa_passed = service.transition(
        UUID(TENANT_ID),
        record.episode_id,
        EpisodeState.QA_PASSED,
        expected_version=rendered.version,
        qa_evidence={"status": "pass"},
    )
    package_bytes = b"package bytes have a different identity"
    package_sha256 = sha256(package_bytes).hexdigest()
    manifest_sha256 = "b" * 64
    packaged = service.transition(
        UUID(TENANT_ID),
        record.episode_id,
        EpisodeState.PACKAGED,
        expected_version=qa_passed.version,
        package_sha256=package_sha256,
        package_bytes=package_bytes,
        package_manifest_sha256=manifest_sha256,
    )

    with TestClient(create_app(service)) as packaged_client:
        response = packaged_client.get(
            f"/v1/episodes/{packaged.episode_id}/status",
            headers=_headers(key="manifest-status-read-001"),
        )

    assert response.status_code == 200
    assert response.json()["package_manifest_sha256"] == manifest_sha256
    assert response.json()["package_manifest_sha256"] != package_sha256


@pytest.mark.parametrize(
    "method,path_suffix",
    [
        ("get", ""),
        ("get", "/status"),
        ("post", "/render"),
        ("post", "/publish"),
    ],
)
def test_cross_tenant_routes_return_redacted_not_found(
    client: TestClient, method: str, path_suffix: str
) -> None:
    episode_id = _create_episode(client)["episode"]["id"]
    response = getattr(client, method)(
        f"/v1/episodes/{episode_id}{path_suffix}",
        headers=_headers(
            tenant_id=OTHER_TENANT_ID, key=f"other-{path_suffix or 'get'}"
        ),
    )

    _assert_problem(response, status=404, code="episode_not_found")


def test_render_is_non_blocking_and_idempotently_reuses_its_receipt(
    client: TestClient,
) -> None:
    episode_id = _create_episode(client)["episode"]["id"]
    headers = _headers(key="render-001")

    first = client.post(f"/v1/episodes/{episode_id}/render", headers=headers)
    second = client.post(f"/v1/episodes/{episode_id}/render", headers=headers)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["episode_id"] == episode_id
    assert first.json()["command"] == "render"
    assert first.json()["accepted"] is True
    assert first.json()["state"] == "queued"


def test_render_replays_accepted_receipt_after_episode_is_published() -> None:
    repository = InMemoryEpisodeRepository()
    service = EpisodeApplicationService(
        repository=repository,
        available_profiles={PROFILE},
    )
    record = service.create_episode(
        EpisodeCreateCommand(
            tenant_id=UUID(TENANT_ID),
            project_id=UUID(PROJECT_ID),
            idempotency_key="published-render-create-001",
            source_bytes=SOURCE.encode("utf-8"),
            profile_name=PROFILE,
        )
    )
    with TestClient(create_app(service)) as test_client:
        headers = _headers(key="fixture-render")
        first = test_client.post(
            f"/v1/episodes/{record.episode_id}/render", headers=headers
        )
        repository.replace(
            UUID(TENANT_ID),
            replace(record, state=EpisodeState.PUBLISHED, version=record.version + 1),
            expected_version=record.version,
        )
        replay = test_client.post(
            f"/v1/episodes/{record.episode_id}/render", headers=headers
        )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()


def test_publish_requires_explicit_authorization_header(client: TestClient) -> None:
    episode_id = _create_episode(client)["episode"]["id"]
    headers = _headers(key="publish-001")

    unauthorized = client.post(f"/v1/episodes/{episode_id}/publish", headers=headers)
    authorized = client.post(
        f"/v1/episodes/{episode_id}/publish",
        headers={**headers, "X-Publish-Authorization": "true"},
    )

    _assert_problem(unauthorized, status=403, code="publish_authorization_required")
    _assert_problem(authorized, status=409, code="invalid_episode_transition")
