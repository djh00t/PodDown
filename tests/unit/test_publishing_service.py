from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from poddown.artifacts import FilesystemArtifactStore
from poddown.packages import EpisodePackage, PackageProvenance
from poddown.publishing import (
    DisclosurePolicy,
    FilesystemPublicationAdapter,
    PublicationAuthorization,
    PublicationConflictError,
    PublicationTarget,
    PublishingAuthorizationError,
    PublishingService,
    PublishingValidationError,
)

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")


def make_package(store: FilesystemArtifactStore, *, qa: str = "pass") -> EpisodePackage:
    data = b"episode bytes"
    ref = store.put("episode.mp3", "audio/mpeg", data)
    return EpisodePackage(
        episode_version_id="018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        files=(ref,),
        provenance=PackageProvenance(
            source_sha256="b" * 64,
            script_version=1,
            profile_version="v1",
            renderer="test",
            qa=qa,
            critical_token_accuracy=1.0,
            final_sha256=sha256(data).hexdigest(),
        ),
    )


def make_target(kind: str = "filesystem") -> PublicationTarget:
    return PublicationTarget(
        tenant_id=TENANT,
        project_id=PROJECT,
        target_id="target-1",
        kind=kind,
        secret_ref="secret://test/publishing",
        show_id="show-1",
        feed_url="https://example.test/feed.xml",
        disclosure=DisclosurePolicy(),
    )


def auth(operation: str = "publish") -> PublicationAuthorization:
    return PublicationAuthorization(
        actor_id="operator-1",
        decision_id=f"decision-{operation}",
        reason="approved",
        operation=operation,
    )


def test_service_replays_one_receipt_and_writes_checksum_bound_bytes(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    service = PublishingService(
        artifact_store=store,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )
    first = service.publish(package, make_target(), auth(), "key-1")
    second = service.publish(package, make_target(), auth(), "key-1")
    assert first == second
    assert (
        tmp_path
        / "published"
        / "tenants"
        / str(TENANT)
        / "projects"
        / str(PROJECT)
        / "target-1"
        / "episode.mp3"
    ).read_bytes() == b"episode bytes"


def test_service_rejects_failed_qa_before_adapter(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store, qa="fail")
    service = PublishingService(artifact_store=store, adapters={})
    with pytest.raises(PublishingValidationError, match="QA"):
        service.publish(package, make_target(), auth(), "key-1")


def test_failed_adapter_attempt_is_resumable_without_duplicate_receipt(
    tmp_path: Path,
) -> None:
    class FlakyAdapter(FilesystemPublicationAdapter):
        attempts = 0

        def publish(self, package, target, artifacts):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("offline provider failure")
            return super().publish(package, target, artifacts)

    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    adapter = FlakyAdapter(tmp_path / "published")
    service = PublishingService(artifact_store=store, adapters={"filesystem": adapter})
    with pytest.raises(RuntimeError, match="offline provider failure"):
        service.publish(package, make_target(), auth(), "retry-key")
    receipt = service.publish(package, make_target(), auth(), "retry-key")
    assert receipt.status == "resumed"
    assert adapter.attempts == 2
    attempt = service.attempt("retry-key", TENANT, PROJECT)
    assert attempt.state == "completed"
    assert attempt.retryable is False
    assert receipt.status == "resumed"


def test_receipt_provenance_contains_target_snapshot_without_secret_value(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    service = PublishingService(
        artifact_store=store,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )
    receipt = service.publish(package, make_target(), auth(), "provenance-key")
    snapshot = receipt.provenance["target"]
    assert snapshot == {
        "target_id": "target-1",
        "kind": "filesystem",
        "show_id": "show-1",
        "feed_url": "https://example.test/feed.xml",
        "visibility": "public",
        "update_policy": "immutable",
        "disclosure": {"spoken": False, "show_notes": False, "platform": False},
        "package_sha256": package.provenance.final_sha256,
        "package_identity": receipt.provenance["package_identity"],
    }
    assert "secret://test/publishing" not in str(receipt.to_dict())


def test_filesystem_failure_after_first_artifact_leaves_no_final_publication(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    adapter = FilesystemPublicationAdapter(tmp_path / "published", fail_after=1)
    service = PublishingService(artifact_store=store, adapters={"filesystem": adapter})
    with pytest.raises(RuntimeError, match="staged publication failure"):
        service.publish(package, make_target(), auth(), "atomic-key")
    assert not (tmp_path / "published" / "tenants").exists()
    adapter.fail_after = None
    receipt = service.publish(package, make_target(), auth(), "atomic-key")
    assert receipt.status == "resumed"


def test_authorized_filesystem_update_and_delete_return_mutation_receipts(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    adapter = FilesystemPublicationAdapter(tmp_path / "published")
    service = PublishingService(artifact_store=store, adapters={"filesystem": adapter})
    receipt = service.publish(package, make_target(), auth(), "mutation-key")
    with pytest.raises(PublishingAuthorizationError):
        service.update(receipt, auth(), "update-key")
    updated = service.update(receipt, auth("update"), "update-key")
    assert updated.status == "updated"
    deleted = service.delete(receipt, auth("delete"), "delete-key")
    assert deleted.status == "deleted"
    assert not (
        tmp_path / "published" / "tenants" / str(TENANT) / str(PROJECT) / "target-1"
    ).exists()


def test_mutation_idempotency_is_operation_and_publication_bound(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    service = PublishingService(
        artifact_store=store,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )
    receipt = service.publish(package, make_target(), auth(), "publish-key")
    other = service.publish(package, make_target(), auth(), "other-key")
    service.update(receipt, auth("update"), "mutation-key")
    with pytest.raises(PublicationConflictError):
        service.delete(receipt, auth("delete"), "mutation-key")
    with pytest.raises(PublicationConflictError):
        service.update(other, auth("update"), "mutation-key")
