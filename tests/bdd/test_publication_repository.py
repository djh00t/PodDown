"""BDD coverage for durable publication attempt and receipt persistence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.publication_repository import (
    PublicationAttemptNotFound,
    PublicationIdentityConflict,
    PublicationOutcomeUncertain,
    PublicationRepositoryError,
    SQLitePublicationReceiptRepository,
)
from poddown.publishing import (
    DisclosurePolicy,
    PublicationAuthorization,
    PublicationReceipt,
    PublicationTarget,
)

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
EPISODE_VERSION_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"
RECORDED_AT = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)

scenarios("../features/publication_repository.feature")


@pytest.fixture
def repository_context(tmp_path: Path) -> dict[str, object]:
    return {"database": tmp_path / "publication-receipts.sqlite3"}


def _target(
    target_id: str = "primary", *, feed_url: str = "https://example.test/feed.xml"
) -> PublicationTarget:
    return PublicationTarget(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        target_id=target_id,
        kind="rss",
        secret_ref="secret://publication-repository",
        show_id="show-1",
        feed_url=feed_url,
        disclosure=DisclosurePolicy(),
    )


def _receipt(
    target: PublicationTarget,
    *,
    tenant_id: UUID = TENANT_ID,
    project_id: UUID = PROJECT_ID,
    episode_version_id: str = EPISODE_VERSION_ID,
) -> PublicationReceipt:
    return PublicationReceipt(
        publication_id="publication-1",
        tenant_id=tenant_id,
        project_id=project_id,
        episode_version_id=episode_version_id,
        target_id=target.target_id,
        idempotency_key="publish-1",
        package_sha256="a" * 64,
        external_id="rss:primary:episode-1",
        status="published",
        authorization=PublicationAuthorization("actor-1", "approval-1", "approved"),
        disclosure=target.disclosure,
        provenance={"adapter": "rss", "target": {"target_id": target.target_id}},
    )


@given("a completed publication receipt recorded locally")
def completed_receipt(repository_context: dict[str, object]) -> None:
    target = _target()
    receipt = _receipt(target)
    repository = SQLitePublicationReceiptRepository(repository_context["database"])
    attempt = repository.begin(receipt, target, RECORDED_AT)
    repository.succeed(attempt, receipt, RECORDED_AT)
    repository_context.update(repository=repository, target=target, receipt=receipt)


@given("a pending publication attempt recorded locally")
def pending_attempt(repository_context: dict[str, object]) -> None:
    target = _target()
    receipt = _receipt(target)
    repository = SQLitePublicationReceiptRepository(repository_context["database"])
    repository_context.update(
        repository=repository,
        target=target,
        receipt=receipt,
        attempt=repository.begin(receipt, target, RECORDED_AT),
    )


@when("I reconstruct the publication receipt repository and begin the same publication")
def reconstruct_and_replay(repository_context: dict[str, object]) -> None:
    repository = SQLitePublicationReceiptRepository(repository_context["database"])
    repository_context["replay"] = repository.begin(
        repository_context["receipt"], repository_context["target"], RECORDED_AT
    )


@when("I begin a different scoped publication with the same idempotency key")
def independent_scope(repository_context: dict[str, object]) -> None:
    target = _target("secondary")
    receipt = _receipt(target)
    repository_context["independent_attempt"] = repository_context["repository"].begin(
        receipt, target, RECORDED_AT
    )


@when("I begin the same scoped publication with a changed target snapshot")
def conflicting_target_snapshot(repository_context: dict[str, object]) -> None:
    with pytest.raises(PublicationIdentityConflict):
        repository_context["repository"].begin(
            repository_context["receipt"],
            _target(feed_url="https://example.test/changed.xml"),
            RECORDED_AT,
        )
    repository_context["conflict"] = True


@when("I record an uncertain provider outcome")
def uncertain_outcome(repository_context: dict[str, object]) -> None:
    repository_context["repository"].mark_uncertain(
        repository_context["attempt"], "provider response was lost", RECORDED_AT
    )


@when("the persisted successful receipt has a stale external reference")
def stale_persisted_receipt(repository_context: dict[str, object]) -> None:
    connection = sqlite3.connect(repository_context["database"])
    try:
        receipt_json = connection.execute(
            "SELECT receipt_json FROM publication_receipts"
        ).fetchone()[0]
        payload = json.loads(receipt_json)
        payload["external_id"] = "rss:primary:another-episode"
        connection.execute(
            "UPDATE publication_receipts SET receipt_json = ?",
            (json.dumps(payload),),
        )
        connection.commit()
    finally:
        connection.close()
    repository = SQLitePublicationReceiptRepository(repository_context["database"])
    with pytest.raises(PublicationRepositoryError):
        repository.begin(
            repository_context["receipt"], repository_context["target"], RECORDED_AT
        )
    repository_context["replay_rejected"] = True


@when("I use a stale publication attempt handle")
def stale_attempt_handle(repository_context: dict[str, object]) -> None:
    attempt = repository_context["attempt"]
    stale_attempt = replace(
        attempt,
        request=replace(
            attempt.request,
            target_snapshot={
                **attempt.request.target_snapshot,
                "feed_url": "https://drift.test",
            },
        ),
    )
    with pytest.raises(PublicationAttemptNotFound):
        repository_context["repository"].succeed(
            stale_attempt, repository_context["receipt"], RECORDED_AT
        )
    repository_context["stale_attempt_rejected"] = True


@when("I record a known provider failure containing sensitive evidence")
def sensitive_known_failure(repository_context: dict[str, object]) -> None:
    raw_error = (
        "Authorization: Bearer authorization-secret "
        "auth=auth-secret auth: auth-colon-secret "
        "api_key=api-key-secret password: password-secret secret=secret-value "
        "token: token-secret credential=credential-secret "
        "https://private.test/path?access_token=url-token&api_key=url-key"
    )
    repository_context["raw_error"] = raw_error
    repository_context["failed"] = repository_context["repository"].fail(
        repository_context["attempt"], raw_error, RECORDED_AT
    )


@when("receipt persistence is forced to fail during success recording")
def forced_receipt_write_failure(repository_context: dict[str, object]) -> None:
    connection = sqlite3.connect(repository_context["database"])
    try:
        connection.execute(
            """CREATE TRIGGER reject_publication_receipt
               BEFORE INSERT ON publication_receipts
               BEGIN SELECT RAISE(ABORT, 'forced receipt write failure'); END"""
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(sqlite3.IntegrityError, match="forced receipt write failure"):
        repository_context["repository"].succeed(
            repository_context["attempt"], repository_context["receipt"], RECORDED_AT
        )


@then("the recorded successful receipt is replayed exactly")
def replayed_exactly(repository_context: dict[str, object]) -> None:
    assert repository_context["replay"].receipt == repository_context["receipt"]
    assert repository_context["replay"].attempt_number == 1


@then("the publication repository fails closed")
def fails_closed(repository_context: dict[str, object]) -> None:
    assert repository_context["conflict"] is True


@then("both scoped publication attempts are retained independently")
def scoped_attempts_are_independent(repository_context: dict[str, object]) -> None:
    attempt = repository_context["independent_attempt"]
    assert attempt.attempt_number == 1
    assert attempt.request.target_id == "secondary"


@then("the same publication cannot begin another provider attempt")
def uncertain_blocks_retry(repository_context: dict[str, object]) -> None:
    with pytest.raises(PublicationOutcomeUncertain):
        repository_context["repository"].begin(
            repository_context["receipt"], repository_context["target"], RECORDED_AT
        )


@then("the publication repository rejects the successful replay")
def successful_replay_rejected(repository_context: dict[str, object]) -> None:
    assert repository_context["replay_rejected"] is True


@then("the stale attempt handle cannot change the publication outcome")
def stale_attempt_cannot_change_outcome(repository_context: dict[str, object]) -> None:
    assert repository_context["stale_attempt_rejected"] is True
    with pytest.raises(PublicationOutcomeUncertain):
        repository_context["repository"].begin(
            repository_context["receipt"], repository_context["target"], RECORDED_AT
        )


@then("the retained failure evidence contains no raw secret")
def failed_evidence_is_safe(repository_context: dict[str, object]) -> None:
    failed = repository_context["failed"]
    assert failed.status == "failed"
    assert failed.error is not None
    for secret in (
        "authorization-secret",
        "auth-secret",
        "auth-colon-secret",
        "api-key-secret",
        "password-secret",
        "secret-value",
        "token-secret",
        "credential-secret",
        "url-token",
        "url-key",
    ):
        assert secret not in failed.error


@then("the publication remains pending without a receipt")
def pending_after_receipt_write_rollback(repository_context: dict[str, object]) -> None:
    connection = sqlite3.connect(repository_context["database"])
    try:
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM publication_receipts"
        ).fetchone()[0]
    finally:
        connection.close()
    assert receipt_count == 0
    with pytest.raises(PublicationOutcomeUncertain):
        repository_context["repository"].begin(
            repository_context["receipt"], repository_context["target"], RECORDED_AT
        )


def test_stale_attempt_handle_fails_closed_without_mutating_the_persisted_attempt(
    tmp_path: Path,
) -> None:
    """A stale target snapshot must not transition a later persisted attempt."""
    target = _target()
    receipt = _receipt(target)
    repository = SQLitePublicationReceiptRepository(tmp_path / "publication.sqlite3")
    attempt = repository.begin(receipt, target, RECORDED_AT)
    stale_attempt = replace(
        attempt,
        request=replace(
            attempt.request,
            target_snapshot={
                **attempt.request.target_snapshot,
                "feed_url": "https://drift.test",
            },
        ),
    )

    with pytest.raises(PublicationAttemptNotFound):
        repository.succeed(stale_attempt, receipt, RECORDED_AT)
    with pytest.raises(PublicationAttemptNotFound):
        repository.fail(stale_attempt, "known provider rejection", RECORDED_AT)
    with pytest.raises(PublicationAttemptNotFound):
        repository.mark_uncertain(
            stale_attempt, "provider response was lost", RECORDED_AT
        )

    with pytest.raises(PublicationOutcomeUncertain):
        repository.begin(receipt, target, RECORDED_AT + timedelta(seconds=1))


def test_failed_attempt_redacts_evidence_and_retries_with_the_next_number(
    tmp_path: Path,
) -> None:
    """A public failure transition retains safe evidence and permits numbered retry."""
    target = _target()
    receipt = _receipt(target)
    repository = SQLitePublicationReceiptRepository(tmp_path / "publication.sqlite3")
    attempt = repository.begin(receipt, target, RECORDED_AT)
    raw_error = (
        "Authorization: Bearer super-secret https://private.test/path?token=abc "
        + "x" * 512
    )

    failed = repository.fail(attempt, raw_error, RECORDED_AT + timedelta(seconds=1))
    retry = repository.begin(receipt, target, RECORDED_AT + timedelta(seconds=2))

    assert failed.status == "failed"
    assert failed.error == "Authorization: [REDACTED] [REDACTED_URL] " + "x" * 215
    assert len(failed.error) == 256
    assert failed.created_at == RECORDED_AT
    assert failed.updated_at == RECORDED_AT + timedelta(seconds=1)
    assert retry.attempt_number == 2
    assert retry.created_at == RECORDED_AT + timedelta(seconds=2)


def test_uncertain_attempt_redacts_all_sensitive_evidence(tmp_path: Path) -> None:
    """An uncertain outcome must retain no provider credentials for review."""
    target = _target()
    receipt = _receipt(target)
    repository = SQLitePublicationReceiptRepository(tmp_path / "publication.sqlite3")
    attempt = repository.begin(receipt, target, RECORDED_AT)
    raw_error = (
        "Bearer bearer-secret auth=auth-secret auth: auth-colon-secret "
        "api-key: api-key-secret password=password-secret "
        "secret: secret-value token=token-secret credential:credential-secret "
        "https://private.test/path?token=url-token"
    )

    uncertain = repository.mark_uncertain(attempt, raw_error, RECORDED_AT)

    assert uncertain.error is not None
    for secret in (
        "bearer-secret",
        "auth-secret",
        "auth-colon-secret",
        "api-key-secret",
        "password-secret",
        "secret-value",
        "token-secret",
        "credential-secret",
        "url-token",
    ):
        assert secret not in uncertain.error
