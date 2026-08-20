"""BDD bindings for the PostgreSQL publication approval adapter."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when
from uuid6 import uuid7

from poddown.approvals import (
    ApprovalNotUsable,
    PostgresApprovalRepository,
    PublicationApproval,
)

scenarios("../features/postgres_approvals.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
OTHER_PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-999999999999")
EPISODE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
NOW = datetime(2026, 8, 14, tzinfo=UTC)


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.rowcount = 0
        self._row: tuple[object, ...] | None = None

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        normalized = " ".join(sql.split()).casefold()
        self.connection.statements.append((sql, params))
        self.rowcount = 0
        self._row = None
        if normalized.startswith("select set_config"):
            return self
        if normalized.startswith("select project_id"):
            key = (str(params[0]), str(params[1]))
            record = self.connection.rows.get(key)
            if record is not None:
                self._row = (
                    record.project_id,
                    record.episode_id,
                    record.target_id,
                    record.operation,
                    record.package_sha256,
                    record.actor_id,
                    record.nonce_sha256,
                    record.issued_at,
                    record.expires_at,
                    record.consumed_at,
                )
            return self
        if normalized.startswith("insert into publication_approvals"):
            key = (str(params[0]), str(params[2]))
            if key not in self.connection.rows:
                self.connection.rows[key] = PublicationApproval(
                    tenant_id=UUID(str(params[0])),
                    project_id=UUID(str(params[1])),
                    approval_id=UUID(str(params[2])),
                    episode_id=UUID(str(params[3])),
                    target_id=str(params[4]),
                    operation=params[5],
                    package_sha256=str(params[6]),
                    actor_id=str(params[7]),
                    nonce_sha256=str(params[8]),
                    issued_at=params[9],
                    expires_at=params[10],
                )
                self.rowcount = 1
            return self
        if normalized.startswith("update publication_approvals"):
            key = (str(params[1]), str(params[2]))
            record = self.connection.rows[key]
            if record.consumed_at is None:
                self.connection.rows[key] = replace(record, consumed_at=params[0])
                self.rowcount = 1
            return self
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], PublicationApproval] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self


def _approval() -> PublicationApproval:
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
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )


@given("a recording PostgreSQL approval repository")
def recording_repository(context: Any) -> None:
    connection = _Connection()
    context.values["connection"] = connection
    context.values["repository"] = PostgresApprovalRepository(lambda: connection)


@when("I issue and consume a PostgreSQL approval")
def issue_and_consume(context: Any) -> None:
    approval = _approval()
    repository = context.values["repository"]
    repository.issue(approval)
    context.values["consumed"] = repository.consume(
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


@then("the PostgreSQL approval is consumed with its audit timestamp")
def consumed_with_audit_timestamp(context: Any) -> None:
    assert context.values["consumed"].consumed_at == NOW + timedelta(minutes=1)
    assert any(
        "set_config('poddown.tenant_id'" in statement
        for statement, _params in context.values["connection"].statements
    )


@when("I issue and consume the approval from another project")
def consume_from_other_project(context: Any) -> None:
    approval = _approval()
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


@then("the PostgreSQL approval consume is rejected")
def consume_rejected(context: Any) -> None:
    assert context.values["repository"]
