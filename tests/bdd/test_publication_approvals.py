"""BDD bindings for durable publication approvals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when
from uuid6 import uuid7

from poddown.approvals import (
    ApprovalAlreadyConsumed,
    ApprovalNotUsable,
    PublicationApproval,
    SQLiteApprovalRepository,
)

scenarios("../features/publication_approvals.feature")


TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
OTHER_PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-999999999999")
EPISODE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
NOW = datetime(2026, 8, 14, tzinfo=UTC)


def _approval(
    *,
    expires_at: datetime,
    issued_at: datetime = NOW,
) -> PublicationApproval:
    return PublicationApproval(
        tenant_id=TENANT,
        project_id=PROJECT,
        approval_id=uuid7(),
        episode_id=EPISODE,
        target_id="minio-demo",
        operation="publish",
        package_sha256="a" * 64,
        actor_id="user-1",
        nonce_sha256="b" * 64,
        issued_at=issued_at,
        expires_at=expires_at,
    )


@given("a durable SQLite approval repository")
def durable_repository(context, tmp_path) -> None:
    context.values["repository"] = SQLiteApprovalRepository(tmp_path / "approvals.db")
    context.values["now"] = NOW


@when("I issue and consume a publish approval twice")
def consume_twice(context) -> None:
    approval = _approval(expires_at=NOW + timedelta(minutes=5))
    repository = context.values["repository"]
    repository.issue(approval)
    context.values["approval"] = repository.consume(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        approval_id=approval.approval_id,
        target_id=approval.target_id,
        operation="publish",
        package_sha256=approval.package_sha256,
        actor_id=approval.actor_id,
        now=NOW + timedelta(minutes=1),
    )
    with pytest.raises(ApprovalAlreadyConsumed):
        repository.consume(
            tenant_id=TENANT,
            project_id=PROJECT,
            episode_id=EPISODE,
            approval_id=approval.approval_id,
            target_id=approval.target_id,
            operation="publish",
            package_sha256=approval.package_sha256,
            actor_id=approval.actor_id,
            now=NOW + timedelta(minutes=2),
        )


@then("the first consumption succeeds and the replay is rejected")
def first_consumption(context) -> None:
    assert context.values["approval"].consumed_at == NOW + timedelta(minutes=1)


@when("I consume an approval from another project")
def consume_other_project(context) -> None:
    approval = _approval(expires_at=NOW + timedelta(minutes=5))
    repository = context.values["repository"]
    repository.issue(approval)
    with pytest.raises(ApprovalNotUsable):
        repository.consume(
            tenant_id=TENANT,
            project_id=OTHER_PROJECT,
            episode_id=EPISODE,
            approval_id=approval.approval_id,
            target_id=approval.target_id,
            operation="publish",
            package_sha256=approval.package_sha256,
            actor_id=approval.actor_id,
            now=NOW + timedelta(minutes=1),
        )


@then("approval consumption is rejected")
def approval_rejected(context) -> None:
    assert context.values["repository"]


@when("I consume an expired approval")
def consume_expired(context) -> None:
    approval = _approval(
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW - timedelta(seconds=1),
    )
    repository = context.values["repository"]
    repository.issue(approval)
    with pytest.raises(ApprovalNotUsable):
        repository.consume(
            tenant_id=TENANT,
            project_id=PROJECT,
            episode_id=EPISODE,
            approval_id=approval.approval_id,
            target_id=approval.target_id,
            operation="publish",
            package_sha256=approval.package_sha256,
            actor_id=approval.actor_id,
            now=NOW,
        )
