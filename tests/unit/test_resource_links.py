"""Unit contracts for resource-link signing controls."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr

from poddown.resource_links import ResourceLinkSigner, ScopedResourceLinkVerifier

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
EPISODE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")


def test_resource_links_require_https_outside_explicit_local_mode() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ResourceLinkSigner(
            SecretStr("resource-link-test-secret-32-bytes-long"),
            base_url="http://resources.example.test",
            ttl_seconds=60,
        )


def test_resource_links_reject_short_secret_and_unbounded_ttl() -> None:
    with pytest.raises(ValueError):
        ResourceLinkSigner(
            SecretStr("short"), base_url="https://example.test", ttl_seconds=60
        )
    with pytest.raises(ValueError):
        ResourceLinkSigner(
            SecretStr("resource-link-test-secret-32-bytes-long"),
            base_url="https://example.test",
            ttl_seconds=0,
        )


def test_scoped_resource_verifier_binds_project_and_episode() -> None:
    signer = ResourceLinkSigner(
        SecretStr("resource-link-test-secret-32-bytes-long"),
        base_url="https://example.test",
        ttl_seconds=60,
    )
    uri = signer.issue(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        resource="audio",
        media_type="audio/mpeg",
        sha256="a" * 64,
        now=datetime.now(UTC),
    )
    verifier = ScopedResourceLinkVerifier(signer, project_id=PROJECT)

    assert verifier.verify(str(TENANT), str(EPISODE), uri) is True
    assert (
        verifier.verify(
            str(TENANT),
            str(UUID("018f2c8b-7b46-7cc5-b2e1-999999999999")),
            uri,
        )
        is False
    )
