"""BDD bindings for S3-backed immutable publication."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pytest_bdd import given, scenarios, then, when

from poddown.artifacts import FilesystemArtifactStore
from poddown.object_storage import S3ObjectStore
from poddown.packages import (
    REQUIRED_PACKAGE_ARTIFACTS,
    EpisodePackage,
    PackageProvenance,
)
from poddown.publishing import (
    DisclosurePolicy,
    PublicationAuthorization,
    PublicationTarget,
    PublishingService,
    S3CompatiblePublicationAdapter,
)
from tests.fakes import FakeS3Client

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")

scenarios("../features/s3_publication.feature")


@given("a verified package and an S3 publishing target")
def package_and_target(context, tmp_path: Path) -> None:
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    data = b"verified episode bytes"
    references = tuple(
        artifacts.put(
            name,
            "audio/mpeg" if name.endswith((".mp3", ".wav")) else "text/plain",
            data if name == "episode.mp3" else name.encode(),
        )
        for name in REQUIRED_PACKAGE_ARTIFACTS
    )
    wav = next(reference for reference in references if reference.name == "episode.wav")
    package = EpisodePackage(
        "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        references,
        PackageProvenance("b" * 64, 1, "v1", "test", "pass", 1.0, wav.sha256),
    )
    client = FakeS3Client()
    store = S3ObjectStore(bucket="poddown-test", client=client)
    adapter = S3CompatiblePublicationAdapter(store)
    target = PublicationTarget(
        TENANT,
        PROJECT,
        "s3-target",
        "s3",
        "secret://fixture",
        "show",
        "https://example.test/feed.xml",
        DisclosurePolicy(),
    )
    context.values.update(
        adapter=adapter,
        client=client,
        data=data,
        package=package,
        service=PublishingService(artifact_store=artifacts, adapters={"s3": adapter}),
        target=target,
    )


@when("I publish the package through S3 object storage")
def publish(context) -> None:
    context.values["receipt"] = context.values["service"].publish(
        context.values["package"],
        context.values["target"],
        PublicationAuthorization("actor", "decision", "approved"),
        "s3-publication-key",
    )


@then("the receipt remains publication-contract compatible and S3 has verified bytes")
def receipt_and_object(context) -> None:
    receipt = context.values["receipt"]
    adapter = context.values["adapter"]
    reference = adapter.final_references["s3-target"]["episode.mp3"]
    assert receipt.external_id == "s3:s3-target:018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"
    assert receipt.status == "published"
    assert (
        context.values["client"].objects[("poddown-test", reference.storage_key)][
            "Body"
        ]
        == context.values["data"]
    )
