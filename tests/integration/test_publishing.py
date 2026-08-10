from pathlib import Path
from uuid import UUID

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
    PublicationTarget,
    PublishingService,
)


def test_filesystem_publication_is_restart_readable(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    refs = tuple(
        store.put(
            name,
            "audio/mpeg" if name.endswith((".mp3", ".wav")) else "text/plain",
            b"restart-safe audio" if name == "episode.wav" else name.encode(),
        )
        for name in REQUIRED_PACKAGE_ARTIFACTS
    )
    wav = next(ref for ref in refs if ref.name == "episode.wav")
    package = EpisodePackage(
        "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        refs,
        PackageProvenance("b" * 64, 1, "v1", "test", "pass", 1.0, wav.sha256),
    )
    target = PublicationTarget(
        UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"),
        UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"),
        "filesystem",
        "filesystem",
        "secret://fixture",
        "show",
        "https://example.test/feed.xml",
        DisclosurePolicy(),
    )
    service = PublishingService(
        artifact_store=store,
        adapters={"filesystem": FilesystemPublicationAdapter(tmp_path / "published")},
    )
    receipt = service.publish(
        package,
        target,
        PublicationAuthorization("actor", "decision", "approved"),
        "restart-key",
    )
    assert receipt.external_id.startswith("filesystem:")
    assert (
        tmp_path
        / "published"
        / "tenants"
        / "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
        / "projects"
        / "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
        / "filesystem"
        / "episode.mp3"
    ).read_bytes() == b"episode.mp3"
