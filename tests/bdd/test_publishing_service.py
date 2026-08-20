"""BDD coverage for durable publishing-service receipt replay."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.artifacts import ArtifactIntegrityError, FilesystemArtifactStore
from poddown.packages import (
    REQUIRED_PACKAGE_ARTIFACTS,
    EpisodePackage,
    PackageProvenance,
)
from poddown.publication_repository import (
    PublicationOutcomeUncertain,
    SQLitePublicationReceiptRepository,
)
from poddown.publishing import (
    DisclosurePolicy,
    FilesystemPublicationAdapter,
    PublicationAdapterError,
    PublicationAuthorization,
    PublicationConflictError,
    PublicationTarget,
    PublishingService,
)

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
TENANT_ID_2 = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")
PROJECT_ID_2 = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b14")

scenarios("../features/publishing_service.feature")


class CountingAdapter(FilesystemPublicationAdapter):
    """Filesystem adapter that records durable replay dispatches."""

    calls = 0

    def publish(self, package, target, artifacts):
        self.calls += 1
        return super().publish(package, target, artifacts)


class UncertainAdapterFailure(RuntimeError):
    """Raw adapter failure whose boundary has established uncertainty."""

    outcome = "uncertain"


@pytest.fixture
def publishing_context(tmp_path: Path) -> dict[str, object]:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    files = tuple(
        store.put(
            name,
            "audio/mpeg" if name.endswith((".mp3", ".wav")) else "text/plain",
            b"episode bytes" if name == "episode.wav" else name.encode(),
        )
        for name in REQUIRED_PACKAGE_ARTIFACTS
    )
    package = EpisodePackage(
        episode_version_id="018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
        files=files,
        provenance=PackageProvenance(
            source_sha256="b" * 64,
            script_version=1,
            profile_version="v1",
            renderer="test",
            qa="pass",
            critical_token_accuracy=1.0,
            final_sha256=next(
                item for item in files if item.name == "episode.wav"
            ).sha256,
        ),
    )
    target = PublicationTarget(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        target_id="target-1",
        kind="filesystem",
        secret_ref="secret://test/publishing",
        show_id="show-1",
        feed_url="https://example.test/feed.xml",
        disclosure=DisclosurePolicy(),
    )
    adapter = CountingAdapter(tmp_path / "published")
    database = tmp_path / "publication-receipts.sqlite3"
    return {
        "adapter": adapter,
        "database": database,
        "package": package,
        "store": store,
        "service": PublishingService(
            artifact_store=store,
            adapters={"filesystem": adapter},
            publication_repository=SQLitePublicationReceiptRepository(database),
        ),
        "target": target,
    }


@given("a filesystem publication stored with a durable receipt repository")
def stored_publication(publishing_context: dict[str, object]) -> None:
    service = publishing_context["service"]
    package = publishing_context["package"]
    target = publishing_context["target"]
    assert isinstance(service, PublishingService)
    assert isinstance(package, EpisodePackage)
    assert isinstance(target, PublicationTarget)
    publishing_context["first"] = service.publish(
        package,
        target,
        PublicationAuthorization("operator-1", "decision-1", "approved"),
        "durable-bdd-key",
    )


@when("a reconstructed service requests the same publication")
def replay_publication(publishing_context: dict[str, object]) -> None:
    package = publishing_context["package"]
    target = publishing_context["target"]
    database = publishing_context["database"]
    assert isinstance(package, EpisodePackage)
    assert isinstance(target, PublicationTarget)
    assert isinstance(database, Path)
    service = PublishingService(
        artifact_store=FilesystemArtifactStore(
            database.parent / "reconstructed-artifacts"
        ),
        adapters={"filesystem": publishing_context["adapter"]},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )
    publishing_context["second"] = service.publish(
        package,
        target,
        PublicationAuthorization("operator-1", "decision-1", "approved"),
        "durable-bdd-key",
    )


@when("a reconstructed service requests a changed package identity")
def changed_replay(publishing_context: dict[str, object]) -> None:
    package = publishing_context["package"]
    target = publishing_context["target"]
    database = publishing_context["database"]
    assert isinstance(package, EpisodePackage)
    assert isinstance(target, PublicationTarget)
    assert isinstance(database, Path)
    service = PublishingService(
        artifact_store=FilesystemArtifactStore(
            database.parent / "reconstructed-artifacts"
        ),
        adapters={"filesystem": publishing_context["adapter"]},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )
    with pytest.raises(PublicationConflictError):
        service.publish(
            replace(
                package, provenance=replace(package.provenance, renderer="changed")
            ),
            target,
            PublicationAuthorization("operator-1", "decision-1", "approved"),
            "durable-bdd-key",
        )
    publishing_context["conflict"] = True


@then("the stored receipt is returned without another filesystem dispatch")
def receipt_replayed(publishing_context: dict[str, object]) -> None:
    assert publishing_context["second"] == publishing_context["first"]
    adapter = publishing_context["adapter"]
    assert isinstance(adapter, CountingAdapter)
    assert adapter.calls == 1


@then("the durable receipt identity conflict is reported")
def receipt_conflict(publishing_context: dict[str, object]) -> None:
    assert publishing_context["conflict"] is True


class FailOnceReadStore:
    """Expose a single known-safe artifact read failure."""

    def __init__(self, store: FilesystemArtifactStore) -> None:
        self.store = store
        self.failed = False

    def read(self, reference: object) -> bytes:
        if not self.failed:
            self.failed = True
            raise ArtifactIntegrityError("temporary artifact read failure")
        return self.store.read(reference)  # type: ignore[arg-type]


def _reconstruct(publishing_context: dict[str, object]) -> PublishingService:
    database = publishing_context["database"]
    store = publishing_context["store"]
    adapter = publishing_context["adapter"]
    assert isinstance(database, Path)
    assert isinstance(store, FilesystemArtifactStore)
    assert isinstance(adapter, FilesystemPublicationAdapter)
    return PublishingService(
        artifact_store=store,
        adapters={"filesystem": adapter},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )


def _publish(publishing_context: dict[str, object], key: str) -> object:
    service = publishing_context["service"]
    package = publishing_context["package"]
    target = publishing_context["target"]
    assert isinstance(service, PublishingService)
    assert isinstance(package, EpisodePackage)
    assert isinstance(target, PublicationTarget)
    return service.publish(
        package,
        target,
        PublicationAuthorization("operator-1", "decision-1", "approved"),
        key,
    )


@given("a durable publication attempt ready for pre-dispatch recovery")
def pre_dispatch_attempt(publishing_context: dict[str, object]) -> None:
    store = publishing_context["store"]
    adapter = publishing_context["adapter"]
    database = publishing_context["database"]
    assert isinstance(store, FilesystemArtifactStore)
    assert isinstance(adapter, FilesystemPublicationAdapter)
    assert isinstance(database, Path)
    publishing_context["service"] = PublishingService(
        artifact_store=FailOnceReadStore(store),
        adapters={"filesystem": adapter},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )
    publishing_context["retry_key"] = "pre-dispatch-bdd-key"


@when("a temporary artifact read failure occurs before dispatch")
def read_failure(publishing_context: dict[str, object]) -> None:
    with pytest.raises(ArtifactIntegrityError):
        _publish(publishing_context, "pre-dispatch-bdd-key")


@given("a filesystem adapter reports a known-not-applied outcome")
def known_not_applied(publishing_context: dict[str, object]) -> None:
    class Adapter(CountingAdapter):
        def publish(self, package, target, artifacts):
            self.calls += 1
            if self.calls == 1:
                raise PublicationAdapterError(
                    "provider rejected before apply", outcome="known_not_applied"
                )
            return super().publish(package, target, artifacts)

    database = publishing_context["database"]
    store = publishing_context["store"]
    assert isinstance(database, Path)
    assert isinstance(store, FilesystemArtifactStore)
    adapter = Adapter(database.parent / "published")
    publishing_context["adapter"] = adapter
    publishing_context["service"] = PublishingService(
        artifact_store=store,
        adapters={"filesystem": adapter},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )
    with pytest.raises(PublicationAdapterError):
        _publish(publishing_context, "known-not-applied-bdd-key")
    publishing_context["retry_key"] = "known-not-applied-bdd-key"


@given("a filesystem adapter reports a compensated failure")
def compensated_failure(publishing_context: dict[str, object]) -> None:
    database = publishing_context["database"]
    store = publishing_context["store"]
    assert isinstance(database, Path)
    assert isinstance(store, FilesystemArtifactStore)
    adapter = FilesystemPublicationAdapter(database.parent / "published", fail_after=1)
    publishing_context["adapter"] = adapter
    publishing_context["service"] = PublishingService(
        artifact_store=store,
        adapters={"filesystem": adapter},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )
    with pytest.raises(PublicationAdapterError) as error:
        _publish(publishing_context, "compensated-bdd-key")
    assert error.value.outcome.value == "compensated"
    adapter.fail_after = None
    publishing_context["retry_key"] = "compensated-bdd-key"


@when("a reconstructed service retries the safe failure")
def reconstructed_safe_retry(publishing_context: dict[str, object]) -> None:
    key = publishing_context["retry_key"]
    assert isinstance(key, str)
    publishing_context["service"] = _reconstruct(publishing_context)
    publishing_context["receipt"] = _publish(publishing_context, key)


@then("the reconstructed publication is resumed")
def reconstructed_resumed(publishing_context: dict[str, object]) -> None:
    receipt = publishing_context["receipt"]
    assert getattr(receipt, "status", None) == "resumed"


@given("a durable uncertain publication outcome")
def uncertain_outcome(publishing_context: dict[str, object]) -> None:
    class Adapter(CountingAdapter):
        def publish(self, package, target, artifacts):
            self.calls += 1
            raise PublicationAdapterError("request timed out", outcome="uncertain")

    database = publishing_context["database"]
    store = publishing_context["store"]
    assert isinstance(database, Path)
    assert isinstance(store, FilesystemArtifactStore)
    adapter = Adapter(database.parent / "published")
    publishing_context["adapter"] = adapter
    publishing_context["service"] = PublishingService(
        artifact_store=store,
        adapters={"filesystem": adapter},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )
    with pytest.raises(PublicationAdapterError):
        _publish(publishing_context, "uncertain-bdd-key")


@when("a reconstructed service retries the uncertain publication")
def reconstructed_uncertain_retry(publishing_context: dict[str, object]) -> None:
    publishing_context["service"] = _reconstruct(publishing_context)
    with pytest.raises(PublicationOutcomeUncertain):
        _publish(publishing_context, "uncertain-bdd-key")


@then("no second filesystem dispatch is attempted")
def no_second_dispatch(publishing_context: dict[str, object]) -> None:
    adapter = publishing_context["adapter"]
    assert isinstance(adapter, CountingAdapter)
    assert adapter.calls == 1


@given(
    "a provider error includes quoted JSON, form, "
    "nested credentials, and structured headers"
)
def credential_error(publishing_context: dict[str, object]) -> None:
    class Adapter(CountingAdapter):
        def publish(self, package, target, artifacts):
            self.calls += 1
            raise UncertainAdapterFailure(
                json.dumps(
                    {
                        "credentials": {
                            "username": "credential-user",
                            "nested": {"refresh_token": "refresh-secret"},
                        },
                        "headers": {
                            "Authorization": "Bearer header-secret",
                            "X-Api-Key": "header-key",
                        },
                        "request_url": (
                            "https://provider.test/publish?access_token=url-secret"
                            "&api_key=url-key"
                        ),
                        "quoted_json": ('{"credentials":{"token":"quoted-secret"}}'),
                    }
                )
                + ' form_credentials={"client_secret":"form-secret",'
                '"password":"form-password"}'
            )

    database = publishing_context["database"]
    store = publishing_context["store"]
    assert isinstance(database, Path)
    assert isinstance(store, FilesystemArtifactStore)
    adapter = Adapter(database.parent / "published")
    publishing_context["adapter"] = adapter
    publishing_context["service"] = PublishingService(
        artifact_store=store,
        adapters={"filesystem": adapter},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )


@when("the service records the uncertain outcome")
def record_uncertain(publishing_context: dict[str, object]) -> None:
    with pytest.raises(PublicationAdapterError) as error:
        _publish(publishing_context, "redaction-bdd-key")
    publishing_context["error"] = str(error.value)


@then("durable and exposed errors contain no credential values")
def credentials_redacted(publishing_context: dict[str, object]) -> None:
    service = publishing_context["service"]
    exposed = publishing_context["error"]
    assert isinstance(service, PublishingService)
    assert isinstance(exposed, str)
    durable = service.attempt("redaction-bdd-key", TENANT_ID, PROJECT_ID).failure or ""
    connection = sqlite3.connect(publishing_context["database"])
    try:
        persisted = connection.execute(
            "SELECT error FROM publication_attempts"
        ).fetchone()[0]
    finally:
        connection.close()
    for secret in (
        "credential-user",
        "refresh-secret",
        "header-secret",
        "header-key",
        "url-secret",
        "url-key",
        "quoted-secret",
        "form-secret",
        "form-password",
    ):
        assert secret not in exposed + durable + persisted


@then("a reconstructed retry is blocked without a second dispatch")
def reconstructed_redaction_retry_is_blocked(
    publishing_context: dict[str, object],
) -> None:
    service = publishing_context["service"]
    package = publishing_context["package"]
    target = publishing_context["target"]
    database = publishing_context["database"]
    adapter = publishing_context["adapter"]
    assert isinstance(service, PublishingService)
    assert isinstance(package, EpisodePackage)
    assert isinstance(target, PublicationTarget)
    assert isinstance(database, Path)
    assert isinstance(adapter, CountingAdapter)
    reconstructed = PublishingService(
        artifact_store=publishing_context["store"],
        adapters={"filesystem": adapter},
        publication_repository=SQLitePublicationReceiptRepository(database),
    )
    with pytest.raises(PublicationOutcomeUncertain):
        reconstructed.publish(
            package,
            target,
            PublicationAuthorization("operator-1", "decision-1", "approved"),
            "redaction-bdd-key",
        )
    assert adapter.calls == 1


@when(
    "the same episode and target key is used for another tenant "
    "and then another project"
)
def composite_scope(publishing_context: dict[str, object]) -> None:
    service = publishing_context["service"]
    package = publishing_context["package"]
    target = publishing_context["target"]
    assert isinstance(service, PublishingService)
    assert isinstance(package, EpisodePackage)
    assert isinstance(target, PublicationTarget)
    authorization = PublicationAuthorization("operator-1", "decision-1", "approved")
    service.publish(
        package,
        replace(target, tenant_id=TENANT_ID_2),
        authorization,
        "durable-bdd-key",
    )
    service.publish(
        package,
        replace(target, project_id=PROJECT_ID_2),
        authorization,
        "durable-bdd-key",
    )


@then("all composite scopes dispatch independently")
def composite_dispatched(publishing_context: dict[str, object]) -> None:
    adapter = publishing_context["adapter"]
    database = publishing_context["database"]
    assert isinstance(adapter, CountingAdapter)
    assert isinstance(database, Path)
    assert adapter.calls == 3
    connection = sqlite3.connect(database)
    try:
        attempts = connection.execute(
            """SELECT tenant_id, project_id, episode_version_id, target_id,
                      idempotency_key, attempt_number
                 FROM publication_attempts
                ORDER BY tenant_id, project_id"""
        ).fetchall()
    finally:
        connection.close()
    assert len(attempts) == 3
    assert {(row[0], row[1]) for row in attempts} == {
        (str(TENANT_ID), str(PROJECT_ID)),
        (str(TENANT_ID_2), str(PROJECT_ID)),
        (str(TENANT_ID), str(PROJECT_ID_2)),
    }
    assert all(
        row[2:]
        == (
            "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11",
            "target-1",
            "durable-bdd-key",
            1,
        )
        for row in attempts
    )
