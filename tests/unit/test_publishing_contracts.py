from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from poddown.publishing import (
    DisclosurePolicy,
    PublicationAuthorization,
    PublicationReceipt,
    PublicationTarget,
    PublishingValidationError,
)

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")


def target() -> PublicationTarget:
    return PublicationTarget(
        tenant_id=TENANT,
        project_id=PROJECT,
        target_id="rss-main",
        kind="rss",
        secret_ref="secret://poddown/rss",
        show_id="show-1",
        feed_url="https://example.test/feed.xml",
        disclosure=DisclosurePolicy(spoken=True, show_notes=True, platform=True),
    )


def authorization(operation: str = "publish") -> PublicationAuthorization:
    return PublicationAuthorization(
        actor_id="operator-1",
        decision_id=f"decision-{operation}",
        reason="approved",
        operation=operation,
    )


def test_publication_receipt_is_immutable_and_contains_provenance() -> None:
    receipt = PublicationReceipt(
        publication_id="pub-1",
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_version_id="018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        target_id=target().target_id,
        idempotency_key="publish-1",
        package_sha256="a" * 64,
        external_id="external-1",
        status="published",
        authorization=authorization(),
        disclosure=target().disclosure,
    )
    with pytest.raises(FrozenInstanceError):
        receipt.status = "changed"  # type: ignore[misc]
    assert receipt.to_dict()["authorization"]["decision_id"] == "decision-publish"


def test_target_rejects_non_secret_reference() -> None:
    with pytest.raises(PublishingValidationError):
        PublicationTarget(
            tenant_id=TENANT,
            project_id=PROJECT,
            target_id="rss-main",
            kind="rss",
            secret_ref="plaintext",
            show_id="show-1",
            feed_url="https://example.test/feed.xml",
            disclosure=DisclosurePolicy(),
        )


def test_target_rejects_path_traversal_identity() -> None:
    with pytest.raises(PublishingValidationError):
        PublicationTarget(
            tenant_id=TENANT,
            project_id=PROJECT,
            target_id="../escape",
            kind="filesystem",
            secret_ref="secret://poddown/rss",
            show_id="show-1",
            feed_url="https://example.test/feed.xml",
            disclosure=DisclosurePolicy(),
        )


def test_update_authorization_is_explicitly_separate_from_publish() -> None:
    publish = authorization()
    update = PublicationAuthorization(
        actor_id="operator-1",
        decision_id="decision-update",
        reason="approved",
        operation="update",
    )
    assert publish.operation == "publish"
    assert update.operation == "update"
