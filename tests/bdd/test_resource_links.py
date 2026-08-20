"""BDD bindings for signed, tenant-scoped resource links."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr
from pytest_bdd import given, scenarios, then, when

from poddown.resource_links import ResourceLinkError, ResourceLinkSigner

scenarios("../features/resource_links.feature")


TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
OTHER_TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-999999999999")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
EPISODE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
NOW = datetime(2026, 8, 14, tzinfo=UTC)


@given("a resource-link signer")
def resource_signer(context) -> None:
    context.values["signer"] = ResourceLinkSigner(
        SecretStr("resource-link-test-secret-32-bytes-long"),
        base_url="https://resources.example.test",
        ttl_seconds=60,
    )


@when("I issue and verify an audio resource link")
def issue_and_verify(context) -> None:
    signer = context.values["signer"]
    uri = signer.issue(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        resource="audio",
        media_type="audio/mpeg",
        sha256="a" * 64,
        now=NOW,
    )
    context.values["uri"] = uri
    context.values["reference"] = signer.verify(
        uri,
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        now=NOW + timedelta(seconds=10),
    )


@then("the link verifies for the same tenant and episode")
def same_scope(context) -> None:
    assert context.values["reference"].resource == "audio"
    assert "resource-link-test-secret" not in context.values["uri"]


@when("I verify the link for another tenant")
def wrong_scope(context) -> None:
    signer = context.values["signer"]
    uri = signer.issue(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        resource="audio",
        media_type="audio/mpeg",
        sha256="a" * 64,
        now=NOW,
    )
    with pytest.raises(ResourceLinkError):
        signer.verify(
            uri,
            tenant_id=OTHER_TENANT,
            project_id=PROJECT,
            episode_id=EPISODE,
            now=NOW,
        )


@then("the resource link is rejected")
def link_rejected(context) -> None:
    assert context.values["signer"]


@when("I verify an expired resource link")
def expired_link(context) -> None:
    signer = context.values["signer"]
    uri = signer.issue(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        resource="manifest",
        media_type="application/json",
        sha256="b" * 64,
        now=NOW,
    )
    with pytest.raises(ResourceLinkError):
        signer.verify(
            uri,
            tenant_id=TENANT,
            project_id=PROJECT,
            episode_id=EPISODE,
            now=NOW + timedelta(seconds=61),
        )
