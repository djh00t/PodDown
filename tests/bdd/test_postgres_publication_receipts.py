"""BDD bindings for the PostgreSQL publication receipt adapter."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when
from uuid6 import uuid7

from poddown.postgres_publication import PostgresPublicationReceiptRepository
from poddown.publishing import (
    DisclosurePolicy,
    PublicationAuthorization,
    PublicationConflictError,
    PublicationReceipt,
)

scenarios("../features/postgres_publication_receipts.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
OTHER_PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-999999999999")
NOW = datetime(2026, 8, 14, tzinfo=UTC)


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.rowcount = 0
        self._row: tuple[object, ...] | None = None

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> _Cursor:
        normalized = " ".join(sql.split()).casefold()
        self.connection.statements.append((sql, params))
        self.rowcount = 0
        self._row = None
        if normalized.startswith("select set_config"):
            return self
        if normalized.startswith("select project_id, publication_id"):
            key = (str(params[0]), str(params[1]), str(params[2]))
            record = self.connection.rows.get(key)
            self._row = record
            return self
        if normalized.startswith("insert into publication_receipts"):
            key = (str(params[0]), str(params[4]), str(params[5]))
            if key not in self.connection.rows:
                authorization = params[9]
                disclosure = params[10]
                provenance = params[11]
                self.connection.rows[key] = (
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[8],
                    params[7],
                    json.loads(authorization)
                    if isinstance(authorization, str)
                    else authorization,
                    json.loads(disclosure)
                    if isinstance(disclosure, str)
                    else disclosure,
                    json.loads(provenance)
                    if isinstance(provenance, str)
                    else provenance,
                    params[12],
                )
                self.rowcount = 1
            return self
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], tuple[object, ...]] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self


def _receipt(
    *, project_id: UUID = PROJECT, package_sha256: str = "a" * 64
) -> PublicationReceipt:
    return PublicationReceipt(
        publication_id=str(uuid7()),
        tenant_id=TENANT,
        project_id=project_id,
        episode_version_id="episode-v1",
        target_id="rss-main",
        idempotency_key="publish-1",
        package_sha256=package_sha256,
        external_id="external-1",
        status="published",
        authorization=PublicationAuthorization(
            actor_id="operator-1",
            decision_id="decision-1",
            reason="approved",
        ),
        disclosure=DisclosurePolicy(spoken=True, show_notes=True, platform=False),
        provenance={"provider": "recorded", "target": {"show_id": "show-1"}},
    )


@given("a recording PostgreSQL publication receipt repository")
def recording_repository(context: Any) -> None:
    connection = _Connection()
    context.values["connection"] = connection
    context.values["repository"] = PostgresPublicationReceiptRepository(
        lambda: connection
    )


@when("I save and replay a publication receipt")
def save_and_replay(context: Any) -> None:
    receipt = _receipt()
    repository = context.values["repository"]
    context.values["saved"] = repository.save(receipt)
    context.values["replayed"] = repository.get_by_idempotency(
        tenant_id=TENANT,
        project_id=PROJECT,
        target_id=receipt.target_id,
        idempotency_key=receipt.idempotency_key,
    )


@then("the replayed receipt preserves its immutable audit evidence")
def replay_preserves_evidence(context: Any) -> None:
    assert context.values["replayed"] == context.values["saved"]
    assert context.values["replayed"].disclosure.spoken is True
    assert context.values["replayed"].provenance["target"]["show_id"] == "show-1"
    assert any(
        "set_config('poddown.tenant_id'" in statement
        for statement, _params in context.values["connection"].statements
    )


@when("I save a conflicting publication receipt")
def save_conflict(context: Any) -> None:
    repository = context.values["repository"]
    repository.save(_receipt())
    with pytest.raises(PublicationConflictError):
        repository.save(_receipt(project_id=OTHER_PROJECT))


@then("the publication idempotency conflict is rejected")
def conflict_rejected(context: Any) -> None:
    assert context.values["repository"]
