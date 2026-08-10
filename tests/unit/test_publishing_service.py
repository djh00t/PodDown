from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Lock
from uuid import UUID

import pytest

from poddown.artifacts import FilesystemArtifactStore
from poddown.packages import (
    REQUIRED_PACKAGE_ARTIFACTS,
    EpisodePackage,
    PackageProvenance,
)
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
    references = tuple(
        store.put(
            name,
            "audio/mpeg" if name.endswith((".mp3", ".wav")) else "text/plain",
            (b"episode bytes" if name == "episode.wav" else name.encode()),
        )
        for name in REQUIRED_PACKAGE_ARTIFACTS
    )
    wav = next(ref for ref in references if ref.name == "episode.wav")
    return EpisodePackage(
        episode_version_id="018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        files=references,
        provenance=PackageProvenance(
            source_sha256="b" * 64,
            script_version=1,
            profile_version="v1",
            renderer="test",
            qa=qa,
            critical_token_accuracy=1.0,
            final_sha256=wav.sha256,
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
        / package.episode_version_id
        / "episode.mp3"
    ).read_bytes() == b"episode.mp3"


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
        tmp_path
        / "published"
        / "tenants"
        / str(TENANT)
        / "projects"
        / str(PROJECT)
        / "target-1"
        / package.episode_version_id
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


def test_concurrent_publication_claims_dispatch_once(tmp_path: Path) -> None:
    class CountingAdapter(FilesystemPublicationAdapter):
        calls = 0
        lock = Lock()
        entered = Event()
        release = Event()

        def publish(self, package, target, artifacts):
            with self.lock:
                self.calls += 1
            self.entered.set()
            assert self.release.wait(timeout=5)
            return super().publish(package, target, artifacts)

    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    adapter = CountingAdapter(tmp_path / "published")
    service = PublishingService(artifact_store=store, adapters={"filesystem": adapter})
    start = Barrier(3)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                lambda: (
                    start.wait(),
                    service.publish(package, make_target(), auth(), "race-key"),
                )[1]
            )
            for _ in range(2)
        ]
        start.wait()
        assert adapter.entered.wait(timeout=5)
        adapter.release.set()
        receipts = [future.result(timeout=5) for future in futures]

    assert receipts[0] == receipts[1]
    assert adapter.calls == 1


def test_concurrent_mutation_retries_dispatch_once(tmp_path: Path) -> None:
    class BlockingAdapter(FilesystemPublicationAdapter):
        calls = 0
        lock = Lock()
        entered = Event()
        release = Event()

        def update(self, receipt):
            with self.lock:
                self.calls += 1
            self.entered.set()
            assert self.release.wait(timeout=5)
            return super().update(receipt)

    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    adapter = BlockingAdapter(tmp_path / "published")
    service = PublishingService(artifact_store=store, adapters={"filesystem": adapter})
    receipt = service.publish(package, make_target(), auth(), "publish-key")
    start = Barrier(3)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                lambda: (
                    start.wait(),
                    service.update(receipt, auth("update"), "update-key"),
                )[1]
            )
            for _ in range(2)
        ]
        start.wait()
        assert adapter.entered.wait(timeout=5)
        adapter.release.set()
        mutations = [future.result(timeout=5) for future in futures]

    assert mutations[0] == mutations[1]
    assert mutations[0].status == "updated"
    assert adapter.calls == 1


def test_mutation_retry_fails_closed_after_an_uncertain_adapter_outcome(
    tmp_path: Path,
) -> None:
    class UncertainAdapter(FilesystemPublicationAdapter):
        calls = 0

        def update(self, receipt):
            self.calls += 1
            raise RuntimeError("provider outcome is unknown")

    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    adapter = UncertainAdapter(tmp_path / "published")
    service = PublishingService(artifact_store=store, adapters={"filesystem": adapter})
    receipt = service.publish(package, make_target(), auth(), "publish-key")

    with pytest.raises(RuntimeError, match="outcome is unknown"):
        service.update(receipt, auth("update"), "update-key")
    with pytest.raises(PublicationConflictError, match="outcome is unknown"):
        service.update(receipt, auth("update"), "update-key")

    assert adapter.calls == 1


def test_replay_requires_complete_package_and_target_identity(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    service = PublishingService(
        artifact_store=store,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )
    service.publish(package, make_target(), auth(), "identity-key")

    changed_provenance = replace(package.provenance, source_sha256="c" * 64)
    changed_package = replace(package, provenance=changed_provenance)
    changed_target = replace(make_target(), feed_url="https://changed.test/feed.xml")

    with pytest.raises(PublicationConflictError):
        service.publish(changed_package, make_target(), auth(), "identity-key")
    with pytest.raises(PublicationConflictError):
        service.publish(package, changed_target, auth(), "identity-key")


def test_publish_rejects_incomplete_package_manifest_before_adapter(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    incomplete = replace(package, files=(package.files[0],))
    service = PublishingService(
        artifact_store=store,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )

    with pytest.raises(PublishingValidationError, match="manifest"):
        service.publish(incomplete, make_target(), auth(), "manifest-key")


def test_mutation_replay_uses_stored_mutation_provenance(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    service = PublishingService(
        artifact_store=store,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )
    receipt = service.publish(package, make_target(), auth(), "mutation-publish")
    first = service.update(receipt, auth("update"), "mutation-replay")

    assert service.update(receipt, auth("update"), "mutation-replay") == first


def test_publication_receipt_provenance_is_deeply_immutable(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package = make_package(store)
    service = PublishingService(
        artifact_store=store,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )
    receipt = service.publish(package, make_target(), auth(), "freeze-key")

    with pytest.raises(TypeError):
        receipt.provenance["target"]["show_id"] = "changed"  # type: ignore[index]
