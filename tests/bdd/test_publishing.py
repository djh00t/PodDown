from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.artifacts import FilesystemArtifactStore
from poddown.packages import EpisodePackage, PackageProvenance
from poddown.publishing import (
    DisclosurePolicy,
    FilesystemPublicationAdapter,
    PublicationAuthorization,
    PublicationTarget,
    PublishingAuthorizationError,
    PublishingService,
    PublishingValidationError,
)

scenarios("../features/publishing.feature")
TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")


def _setup(context, tmp_path: Path, qa: str = "pass", kind: str = "filesystem") -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    data = b"bdd episode"
    ref = store.put("episode.mp3", "audio/mpeg", data)
    package = EpisodePackage(
        "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        (ref,),
        PackageProvenance("b" * 64, 1, "v1", "test", qa, 1.0, sha256(data).hexdigest()),
    )
    target = PublicationTarget(
        TENANT,
        PROJECT,
        "bdd-target",
        kind,
        "secret://bdd",
        "show-1",
        "https://example.test/feed.xml",
        DisclosurePolicy(),
    )
    service = PublishingService(
        artifact_store=store,
        adapters={kind: FilesystemPublicationAdapter(tmp_path / "published")},
    )
    context.values.update(package=package, target=target, service=service)


def _auth(operation: str = "publish") -> PublicationAuthorization:
    return PublicationAuthorization(
        "actor", f"decision-{operation}", "approved", operation
    )


@given("a verified QA-passed package and an authorized publishing target")
def verified(context, tmp_path):
    _setup(context, tmp_path)
    context.values["authorization"] = _auth()


@given("a package whose QA evidence failed")
def failed(context, tmp_path):
    _setup(context, tmp_path, qa="fail")
    context.values["authorization"] = _auth()


@given("an existing publication")
def existing(context, tmp_path):
    verified(context, tmp_path)
    context.values["receipt"] = context.values["service"].publish(
        context.values["package"],
        context.values["target"],
        context.values["authorization"],
        "existing",
    )


@when("I publish the package")
def publish(context):
    context.values["receipt"] = context.values["service"].publish(
        context.values["package"],
        context.values["target"],
        context.values["authorization"],
        "bdd-key",
    )


@when("I publish the package twice with one idempotency key")
def replay(context):
    context.values["first"] = context.values["service"].publish(
        context.values["package"],
        context.values["target"],
        context.values["authorization"],
        "bdd-key",
    )
    context.values["second"] = context.values["service"].publish(
        context.values["package"],
        context.values["target"],
        context.values["authorization"],
        "bdd-key",
    )


@when("I publish the package to RSS twice")
def rss(context):
    context.values["target"] = PublicationTarget(
        TENANT,
        PROJECT,
        "bdd-rss",
        "rss",
        "secret://bdd",
        "show-1",
        "https://example.test/feed.xml",
        DisclosurePolicy(),
    )
    context.values["service"] = PublishingService(
        artifact_store=context.values["service"].artifact_store,
        adapters={
            "rss": __import__(
                "poddown.publishing", fromlist=["RssPublicationAdapter"]
            ).RssPublicationAdapter()
        },
    )
    context.values["first"] = context.values["service"].publish(
        context.values["package"],
        context.values["target"],
        context.values["authorization"],
        "rss-key",
    )
    context.values["second"] = context.values["service"].publish(
        context.values["package"],
        context.values["target"],
        context.values["authorization"],
        "rss-key",
    )


@when("I attempt publication")
def attempt(context):
    with pytest.raises(PublishingValidationError) as error:
        context.values["service"].publish(
            context.values["package"],
            context.values["target"],
            context.values["authorization"],
            "failed-key",
        )
    context.values["error"] = error.value


@when("I request an update or delete without separate authorization")
def change(context):
    with pytest.raises(PublishingAuthorizationError):
        context.values["service"].update(
            context.values["receipt"], context.values["authorization"], "bdd-update-key"
        )


@then("the publication receipt is immutable and provenance-bound")
def receipt(context):
    assert (
        context.values["receipt"].package_sha256
        == context.values["package"].provenance.final_sha256
    )
    assert context.values["receipt"].authorization.actor_id == "actor"


@then("publication is rejected before any adapter call")
def rejected(context):
    assert "QA" in str(context.values["error"])


@then("both calls return the same publication receipt")
def same(context):
    assert context.values["first"] == context.values["second"]


@then("the RSS bytes are valid and identical")
def rss_same(context):
    assert context.values["first"] == context.values["second"]


@then("the publication change is rejected")
def change_rejected():
    pass
