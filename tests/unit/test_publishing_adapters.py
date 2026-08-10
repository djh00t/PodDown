import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from poddown.artifacts import FilesystemArtifactStore
from poddown.object_storage import FilesystemObjectStore, storage_key_for
from poddown.packages import EpisodePackage, PackageProvenance
from poddown.publishing import (
    DisclosurePolicy,
    FilesystemPublicationAdapter,
    PublicationConflictError,
    PublicationTarget,
    RecordedTransistorAdapter,
    RssPublicationAdapter,
    S3CompatiblePublicationAdapter,
)

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")


def _package(store: FilesystemArtifactStore) -> tuple[EpisodePackage, dict[str, bytes]]:
    data = b"adapter bytes"
    ref = store.put("episode.mp3", "audio/mpeg", data)
    package = EpisodePackage(
        "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        (ref,),
        PackageProvenance(
            "b" * 64, 1, "v1", "test", "pass", 1.0, sha256(data).hexdigest()
        ),
    )
    return package, {"episode.mp3": data}


def _target(kind: str) -> PublicationTarget:
    return PublicationTarget(
        TENANT,
        PROJECT,
        "target",
        kind,
        "secret://fixture",
        "show",
        "https://example.test/feed.xml",
        DisclosurePolicy(),
    )


def test_s3_compatible_adapter_preserves_tenant_scoped_checksum(tmp_path: Path) -> None:
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    package, artifacts = _package(artifact_store)
    object_store = FilesystemObjectStore(tmp_path / "objects")
    adapter = S3CompatiblePublicationAdapter(object_store)
    external_id = adapter.publish(package, _target("s3"), artifacts)
    ref = adapter.references[("target", "episode.mp3")]
    assert external_id == "s3:target:018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"
    assert object_store.read(TENANT, PROJECT, ref) == artifacts["episode.mp3"]


def test_s3_failure_after_first_artifact_has_no_completed_publication(
    tmp_path: Path,
) -> None:
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    package, artifacts = _package(artifact_store)
    object_store = FilesystemObjectStore(tmp_path / "objects")
    adapter = S3CompatiblePublicationAdapter(object_store)
    adapter.fail_after = 1
    with pytest.raises(RuntimeError, match="object publication failure"):
        adapter.publish(package, _target("s3"), artifacts)
    assert "target" not in adapter.completed
    adapter.fail_after = None
    adapter.publish(package, _target("s3"), artifacts)
    assert "target" in adapter.completed


def test_s3_attempt_namespace_promotes_only_after_all_artifacts(tmp_path: Path) -> None:
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    package, artifacts = _package(artifact_store)
    artifacts = {**artifacts, "transcript.txt": b"transcript"}
    object_store = FilesystemObjectStore(tmp_path / "objects")
    adapter = S3CompatiblePublicationAdapter(object_store)
    adapter.fail_after = 1
    with pytest.raises(RuntimeError):
        adapter.publish(package, _target("s3"), artifacts)
    assert adapter.final_references == {}
    assert adapter.staged_references == {}


def test_s3_failure_preserves_content_addressed_objects_without_publication_reference(
    tmp_path: Path,
) -> None:
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    package, artifacts = _package(artifact_store)
    object_store = FilesystemObjectStore(tmp_path / "objects")
    adapter = S3CompatiblePublicationAdapter(object_store)
    reused = object_store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=artifacts["episode.mp3"],
    )
    adapter.fail_after = 2
    artifacts = {**artifacts, "transcript.txt": b"new partial"}
    with pytest.raises(RuntimeError):
        adapter.publish(package, _target("s3"), artifacts)
    assert object_store.read(TENANT, PROJECT, reused) == artifacts["episode.mp3"]
    partial_digest = sha256(b"new partial").hexdigest()
    partial_path = (
        tmp_path / "objects" / storage_key_for(TENANT, PROJECT, partial_digest)
    )
    assert partial_path.exists()
    adapter.fail_after = None
    adapter.publish(package, _target("s3"), artifacts)
    assert len(adapter.final_references["target"]) == 2
    assert adapter.staged_references == {}


def test_same_target_id_isolated_by_filesystem_scope(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package, artifacts = _package(store)
    adapter = FilesystemPublicationAdapter(tmp_path / "published")
    other_tenant = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11")
    other = PublicationTarget(
        other_tenant,
        PROJECT,
        "target",
        "filesystem",
        "secret://fixture",
        "show",
        "https://example.test/feed.xml",
        DisclosurePolicy(),
    )
    adapter.publish(package, _target("filesystem"), artifacts)
    adapter.publish(package, other, {"episode.mp3": b"other bytes"})
    assert (
        tmp_path
        / "published"
        / "tenants"
        / str(TENANT)
        / "projects"
        / str(PROJECT)
        / "target"
        / package.episode_version_id
        / "episode.mp3"
    ).read_bytes() == artifacts["episode.mp3"]
    assert (
        tmp_path
        / "published"
        / "tenants"
        / str(other_tenant)
        / "projects"
        / str(PROJECT)
        / "target"
        / package.episode_version_id
        / "episode.mp3"
    ).read_bytes() == b"other bytes"


def test_filesystem_target_keeps_distinct_episode_packages(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    first, artifacts = _package(store)
    second = EpisodePackage(
        "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13",
        first.files,
        first.provenance,
    )
    adapter = FilesystemPublicationAdapter(tmp_path / "published")

    adapter.publish(first, _target("filesystem"), artifacts)
    adapter.publish(second, _target("filesystem"), artifacts)

    destination = (
        tmp_path
        / "published"
        / "tenants"
        / str(TENANT)
        / "projects"
        / str(PROJECT)
        / "target"
    )
    assert (destination / first.episode_version_id / "episode.mp3").read_bytes() == (
        b"adapter bytes"
    )
    assert (destination / second.episode_version_id / "episode.mp3").read_bytes() == (
        b"adapter bytes"
    )


def test_rss_adapter_is_valid_and_byte_deterministic(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package, artifacts = _package(store)
    adapter = RssPublicationAdapter()
    first = adapter.publish(package, _target("rss"), artifacts)
    second = adapter.publish(package, _target("rss"), artifacts)
    assert first == second
    root = ET.fromstring(adapter.feed(_target("rss")))
    assert root.tag == "rss"
    assert root.find("./channel/item/guid").text == package.episode_version_id


def test_rss_history_isolated_by_tenant_project_and_target(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package, artifacts = _package(store)
    adapter = RssPublicationAdapter()
    adapter.publish(package, _target("rss"), artifacts)
    other = PublicationTarget(
        UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
        PROJECT,
        "target",
        "rss",
        "secret://fixture",
        "show",
        "https://example.test/feed.xml",
        DisclosurePolicy(),
    )
    adapter.publish(package, other, artifacts)
    assert len(adapter.feeds) == 2


def test_rss_adapter_preserves_two_guided_history_and_rejects_conflict(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    first, artifacts = _package(store)
    second_ref = store.put("episode.mp3", "audio/mpeg", b"second episode")
    second = EpisodePackage(
        "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13", (second_ref,), first.provenance
    )
    adapter = RssPublicationAdapter()
    adapter.publish(first, _target("rss"), artifacts)
    adapter.publish(second, _target("rss"), {"episode.mp3": b"second episode"})
    root = ET.fromstring(adapter.feed(_target("rss")))
    assert [node.text for node in root.findall("./channel/item/guid")] == [
        first.episode_version_id,
        second.episode_version_id,
    ]
    with pytest.raises(PublicationConflictError):
        adapter.publish(first, _target("rss"), {"episode.mp3": b"changed"})


def test_transistor_adapter_uses_recorded_fixture_only(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    package, artifacts = _package(store)
    adapter = RecordedTransistorAdapter({"id": "transistor-episode-1"})
    assert (
        adapter.publish(package, _target("transistor"), artifacts)
        == "transistor-episode-1"
    )
