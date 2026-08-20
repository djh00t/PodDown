from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import SecretStr

from poddown.api import create_app
from poddown.episode_service import (
    EpisodeApplicationService,
    EpisodeCreateCommand,
    EpisodeState,
    InMemoryEpisodeRepository,
)
from poddown.resource_links import ResourceLinkSigner

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
EPISODE = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b14")
RESOURCE = b"signed manifest bytes"


def _signer() -> ResourceLinkSigner:
    return ResourceLinkSigner(
        SecretStr("resource-link-test-secret-32-bytes-long"),
        base_url="https://testserver",
        ttl_seconds=60,
    )


def _headers(key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": str(TENANT),
        "X-Project-ID": str(PROJECT),
        "Idempotency-Key": key,
    }


def _uri(signer: ResourceLinkSigner, data: bytes = RESOURCE) -> str:
    return signer.issue(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        resource="manifest",
        media_type="application/json",
        sha256=sha256(data).hexdigest(),
        now=datetime.now(UTC),
    )


def test_signed_resource_route_returns_exact_checksum_bound_bytes() -> None:
    signer = _signer()
    with TestClient(
        create_app(resource_link_signer=signer, resource_reader=lambda _ref: RESOURCE),
        base_url="https://testserver",
    ) as client:
        response = client.get(_uri(signer), headers=_headers("resource-api-001"))

    assert response.status_code == 200
    assert response.content == RESOURCE
    assert response.headers["content-type"].startswith("application/json")


def test_signed_resource_route_rejects_checksum_mismatch_without_returning_data() -> (
    None
):
    signer = _signer()
    with TestClient(
        create_app(resource_link_signer=signer, resource_reader=lambda _ref: b"wrong"),
        base_url="https://testserver",
    ) as client:
        response = client.get(_uri(signer), headers=_headers("resource-api-002"))

    assert response.status_code == 502
    assert response.json()["code"] == "resource_integrity_failed"
    assert response.content != RESOURCE


def test_signed_resource_route_fails_closed_when_storage_boundary_is_unconfigured() -> (
    None
):
    signer = _signer()
    with TestClient(
        create_app(resource_link_signer=signer), base_url="https://testserver"
    ) as client:
        response = client.get(_uri(signer), headers=_headers("resource-api-003"))

    assert response.status_code == 503
    assert response.json()["code"] == "resource_store_unavailable"


def test_episode_summary_exposes_a_signed_manifest_link_when_packaged() -> None:
    repository = InMemoryEpisodeRepository()
    service = EpisodeApplicationService(
        repository=repository,
        available_profiles={"technical-dialogue"},
    )
    source = b"---\npoddown:\n  profile: technical-dialogue\n---\n# Resource\n"
    created = service.create_episode(
        EpisodeCreateCommand(
            tenant_id=TENANT,
            project_id=PROJECT,
            idempotency_key="resource-summary-001",
            source_bytes=source,
            profile_name="technical-dialogue",
        )
    )
    scripted = service.transition(
        TENANT,
        created.episode_id,
        EpisodeState.SCRIPTED,
        expected_version=created.version,
    )
    rendered = service.transition(
        TENANT,
        created.episode_id,
        EpisodeState.RENDERED,
        expected_version=scripted.version,
    )
    qa_passed = service.transition(
        TENANT,
        created.episode_id,
        EpisodeState.QA_PASSED,
        expected_version=rendered.version,
        qa_evidence={"status": "pass"},
    )
    manifest_sha256 = "c" * 64
    packaged = service.transition(
        TENANT,
        created.episode_id,
        EpisodeState.PACKAGED,
        expected_version=qa_passed.version,
        package_sha256=sha256(b"package bytes").hexdigest(),
        package_bytes=b"package bytes",
        package_manifest_sha256=manifest_sha256,
    )
    signer = _signer()

    with TestClient(
        create_app(service, resource_link_signer=signer),
        base_url="https://testserver",
    ) as client:
        response = client.get(
            f"/v1/episodes/{packaged.episode_id}",
            headers=_headers("resource-summary-read-001"),
        )

    assert response.status_code == 200
    resources = response.json()["resources"]
    assert len(resources) == 1
    reference = signer.verify(
        resources[0]["uri"],
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=packaged.episode_id,
        now=datetime.now(UTC),
    )
    assert reference.resource == "manifest"
    assert reference.media_type == "application/json"
    assert reference.sha256 == manifest_sha256
