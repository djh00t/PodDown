"""Integration coverage for object-backed deterministic RSS publication."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from poddown.artifacts import FilesystemArtifactStore
from poddown.object_storage import (
    FilesystemObjectStore,
    ObjectIntegrityError,
    S3ObjectStore,
)
from poddown.packages import EpisodePackage, PackageProvenance
from poddown.publishing import (
    DisclosurePolicy,
    PublicationTarget,
    PublishingValidationError,
    RssPublicationAdapter,
)
from tests.fakes import FakeS3Client

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")


def _package(store: FilesystemArtifactStore) -> tuple[EpisodePackage, dict[str, bytes]]:
    audio = b"deterministic RSS audio"
    artifact = store.put("episode.mp3", "audio/mpeg", audio)
    return (
        EpisodePackage(
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
            (artifact,),
            PackageProvenance(
                "b" * 64, 1, "v1", "test", "pass", 1.0, sha256(audio).hexdigest()
            ),
        ),
        {"episode.mp3": audio},
    )


def _target() -> PublicationTarget:
    return PublicationTarget(
        TENANT,
        PROJECT,
        "rss-object-store",
        "rss",
        "secret://fixture",
        "show",
        "https://example.test/feed.xml",
        DisclosurePolicy(),
    )


class FailOnceCanonicalRssStore(FilesystemObjectStore):
    """Fail one canonical RSS write while preserving normal object-store behavior."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.fail_next_canonical_write = True

    def put(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ):
        if name == "rss.xml" and self.fail_next_canonical_write:
            self.fail_next_canonical_write = False
            raise RuntimeError("temporary canonical RSS write failure")
        return super().put(
            tenant_id,
            project_id,
            name=name,
            media_type=media_type,
            data=data,
        )


def test_failed_canonical_rss_write_does_not_leak_into_later_feed(
    tmp_path: Path,
) -> None:
    package, artifacts = _package(FilesystemArtifactStore(tmp_path / "artifacts"))
    later_package = replace(
        package, episode_version_id="018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"
    )
    adapter = RssPublicationAdapter(FailOnceCanonicalRssStore(tmp_path / "objects"))
    target = _target()

    with pytest.raises(RuntimeError, match="temporary canonical RSS write failure"):
        adapter.publish(package, target, artifacts)

    adapter.publish(later_package, target, artifacts)

    feed = adapter.feed(target)
    assert package.episode_version_id.encode() not in feed
    assert feed.count(b"<item>") == 1


def test_rss_replay_rejects_mismatched_existing_object_bytes(tmp_path: Path) -> None:
    package, artifacts = _package(FilesystemArtifactStore(tmp_path / "artifacts"))
    object_store = FilesystemObjectStore(tmp_path / "objects")
    adapter = RssPublicationAdapter(object_store)
    target = _target()

    adapter.publish(package, target, artifacts)
    reference = adapter.rss_reference(target)
    assert reference is not None
    object_path = tmp_path / "objects" / reference.storage_key
    object_path.write_bytes(b"mismatched RSS bytes")

    with pytest.raises(ObjectIntegrityError, match="bytes do not match"):
        adapter.publish(package, target, artifacts)


def test_reconstructed_rss_adapter_retains_prior_guid_from_object_store(
    tmp_path: Path,
) -> None:
    package, artifacts = _package(FilesystemArtifactStore(tmp_path / "artifacts"))
    object_store = FilesystemObjectStore(tmp_path / "objects")
    target = _target()
    first = RssPublicationAdapter(object_store)
    first.publish(package, target, artifacts)

    second_package = replace(
        package, episode_version_id="018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"
    )
    restarted = RssPublicationAdapter(object_store)
    restarted.publish(second_package, target, artifacts)

    feed = restarted.feed(target)
    assert package.episode_version_id.encode() in feed
    assert second_package.episode_version_id.encode() in feed


def test_reconstructed_rss_adapter_discovers_prior_guid_from_s3_object_store(
    tmp_path: Path,
) -> None:
    package, artifacts = _package(FilesystemArtifactStore(tmp_path / "artifacts"))
    object_store = S3ObjectStore(bucket="poddown-test", client=FakeS3Client())
    target = _target()
    RssPublicationAdapter(object_store).publish(package, target, artifacts)

    second_package = replace(
        package, episode_version_id="018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"
    )
    restarted = RssPublicationAdapter(object_store)
    restarted.publish(second_package, target, artifacts)

    assert package.episode_version_id.encode() in restarted.feed(target)


def test_reconstructed_rss_adapter_rejects_malformed_state_from_filesystem(
    tmp_path: Path,
) -> None:
    package, artifacts = _package(FilesystemArtifactStore(tmp_path / "artifacts"))
    object_store = FilesystemObjectStore(tmp_path / "objects")
    target = _target()
    RssPublicationAdapter(object_store).publish(package, target, artifacts)
    object_store.put(
        TENANT,
        PROJECT,
        name="rss-state-rss-object-store-00000000000000000002",
        media_type="application/json",
        data=b"{malformed",
    )

    with pytest.raises(PublishingValidationError, match="RSS state object is invalid"):
        RssPublicationAdapter(object_store).publish(package, target, artifacts)


def test_reconstructed_rss_adapter_rejects_malformed_state_from_s3(
    tmp_path: Path,
) -> None:
    package, artifacts = _package(FilesystemArtifactStore(tmp_path / "artifacts"))
    object_store = S3ObjectStore(bucket="poddown-test", client=FakeS3Client())
    target = _target()
    RssPublicationAdapter(object_store).publish(package, target, artifacts)
    object_store.put(
        TENANT,
        PROJECT,
        name="rss-state-rss-object-store-00000000000000000002",
        media_type="application/json",
        data=b"{malformed",
    )

    with pytest.raises(PublishingValidationError, match="RSS state object is invalid"):
        RssPublicationAdapter(object_store).publish(package, target, artifacts)
