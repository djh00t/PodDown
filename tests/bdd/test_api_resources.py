from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import SecretStr
from pytest_bdd import given, scenarios, then, when

from poddown.api import create_app
from poddown.resource_links import ResourceLinkSigner

scenarios("../features/api_resources.feature")

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
EPISODE = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b14")
RESOURCE = b"signed manifest bytes"


def _headers(key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": str(TENANT),
        "X-Project-ID": str(PROJECT),
        "Idempotency-Key": key,
    }


@given("an offline episode API client with a signed resource")
def signed_resource_client(context: Any) -> None:
    signer = ResourceLinkSigner(
        SecretStr("resource-link-test-secret-32-bytes-long"),
        base_url="https://testserver",
        ttl_seconds=60,
    )
    uri = signer.issue(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        resource="manifest",
        media_type="application/json",
        sha256=sha256(RESOURCE).hexdigest(),
        now=datetime.now(UTC),
    )
    context.values["uri"] = uri
    context.values["client"] = TestClient(
        create_app(resource_link_signer=signer, resource_reader=lambda _ref: RESOURCE),
        base_url="https://testserver",
    )


@when("I request the signed resource URL")
def request_resource(context: Any) -> None:
    context.values["response"] = context.values["client"].get(
        context.values["uri"], headers=_headers("resource-bdd-001")
    )


@then("the API returns the exact resource bytes and media type")
def assert_resource(context: Any) -> None:
    response = context.values["response"]
    assert response.status_code == 200
    assert response.content == RESOURCE
    assert response.headers["content-type"].startswith("application/json")


@when("I tamper with the signed resource URL")
def tamper_resource(context: Any) -> None:
    context.values["response"] = context.values["client"].get(
        context.values["uri"].replace("sha256=", "sha256=" + "0"),
        headers=_headers("resource-bdd-002"),
    )


@then("the API rejects the resource without returning bytes")
def assert_rejected_resource(context: Any) -> None:
    response = context.values["response"]
    assert response.status_code == 404
    assert response.content != RESOURCE
