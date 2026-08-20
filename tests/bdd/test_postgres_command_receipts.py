"""BDD bindings for durable PostgreSQL command receipts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.api.models import CommandReceipt
from poddown.api.temporal_dispatcher import CommandIdentity
from poddown.episode_service import IdempotencyConflict
from poddown.postgres_receipts import PostgresCommandReceiptStore

scenarios("../features/postgres_command_receipts.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
EPISODE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
COMMAND_ID = UUID("018f2c8b-7b46-7cc5-b2e1-444444444444")
CREATED = datetime(2026, 8, 14, tzinfo=UTC)
IDENTITY: CommandIdentity = (PROJECT, EPISODE, "render")


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
        if normalized.startswith("select project_id"):
            if "where tenant_id = %s and idempotency_key" in normalized:
                self._row = self.connection.rows.get((str(params[0]), str(params[1])))
            else:
                self._row = next(
                    (
                        row
                        for (tenant, _idempotency), row in self.connection.rows.items()
                        if tenant == str(params[0])
                        and row[0] == str(params[1])
                        and row[1] == str(params[2])
                        and row[3] == params[3]
                        and row[4] == params[4]
                    ),
                    None,
                )
            return self
        if normalized.startswith("insert into command_receipts"):
            tenant, project, episode, command, idempotency = map(str, params[:5])
            key = (tenant, idempotency)
            if key not in self.connection.rows:
                self.connection.rows[key] = (
                    project,
                    episode,
                    params[5],
                    command,
                    idempotency,
                    params[6],
                    params[7],
                    params[10],
                    params[8],
                    params[9],
                    params[11],
                )
                self.rowcount = 1
            return self
        if normalized.startswith("update command_receipts"):
            state, workflow_id, updated_at, tenant, idempotency = params
            key = (str(tenant), str(idempotency))
            row = self.connection.rows[key]
            if row[6] == "queued":
                self.connection.rows[key] = (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    state,
                    workflow_id,
                    row[8],
                    updated_at,
                    row[10],
                )
                self.rowcount = 1
            return self
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], tuple[object, ...]] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self


def _receipt(*, state: str = "queued", workflow_id: str | None = "workflow-1"):
    return CommandReceipt(
        command_id=COMMAND_ID,
        episode_id=EPISODE,
        idempotency_key="render-1",
        command="render",
        state=state,
        workflow_id=workflow_id,
        created_at=CREATED,
    )


@given("a recording PostgreSQL command receipt store")
def recording_store(context: Any) -> None:
    connection = _Connection()
    context.values["connection"] = connection
    context.values["store"] = PostgresCommandReceiptStore(lambda: connection)


@when("I reserve and dispatch a PostgreSQL command receipt")
def reserve_and_dispatch(context: Any) -> None:
    store = context.values["store"]
    queued = _receipt()
    first = store.reserve(
        tenant_id=TENANT,
        idempotency_key="render-1",
        identity=IDENTITY,
        receipt=queued,
        payload={"mode": "deterministic-local"},
    )
    dispatched = first.model_copy(
        update={"state": "dispatched", "workflow_id": "workflow-1"}
    )
    context.values["result"] = store.reconcile_dispatched(
        tenant_id=TENANT,
        idempotency_key="render-1",
        identity=IDENTITY,
        receipt=dispatched,
    )
    context.values["replay"] = store.replay(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        command="render",
        idempotency_key="render-1",
        payload={"mode": "deterministic-local"},
    )


@then("the PostgreSQL receipt replays as dispatched")
def receipt_replays(context: Any) -> None:
    result = context.values["result"]
    replay = context.values["replay"]
    assert result.state == "dispatched"
    assert replay == result
    assert (
        len(
            [
                s
                for s in context.values["connection"].statements
                if "INSERT INTO" in s[0]
            ]
        )
        == 1
    )


@when("I reserve the same PostgreSQL receipt with a different payload")
def reserve_conflicting_payload(context: Any) -> None:
    store = context.values["store"]
    store.reserve(
        tenant_id=TENANT,
        idempotency_key="render-1",
        identity=IDENTITY,
        receipt=_receipt(),
        payload={"mode": "deterministic-local"},
    )
    with pytest.raises(IdempotencyConflict):
        store.reserve(
            tenant_id=TENANT,
            idempotency_key="render-1",
            identity=IDENTITY,
            receipt=_receipt(),
            payload={"mode": "live-provider"},
        )


@then("the PostgreSQL receipt collision is rejected")
def receipt_collision_rejected(context: Any) -> None:
    assert context.values["connection"].rows
