"""BDD bindings for PostgreSQL provider evidence and usage replay."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.persistence import UsageEvent
from poddown.postgres_ledger import PostgresEvidenceLedger
from poddown.providers.contracts import ProviderEvidence

scenarios("../features/postgres_ledger.feature")


TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
JOB = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
OCCURRED = datetime(2026, 8, 14, tzinfo=UTC)


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.rows: list[tuple[Any, ...]] = []
        self.rowcount = 0

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        normalized = " ".join(sql.split()).casefold()
        self.connection.statements.append((sql, params))
        self.rows = []
        self.rowcount = 0
        request_id = str(params[-1]) if params else ""
        if normalized.startswith("select project_id, job_id, request_id"):
            evidence = self.connection.evidence.get(request_id)
            if evidence is not None:
                self.rows = [evidence]
        elif normalized.startswith("select project_id, job_id, provider_request_id"):
            usage = self.connection.usage.get(request_id)
            if usage is not None:
                self.rows = [usage]
        elif normalized.startswith("insert into provider_evidence"):
            request_id = str(params[3])
            if request_id not in self.connection.evidence:
                self.connection.evidence[request_id] = (
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[7],
                    params[8],
                    params[9],
                    params[10],
                    params[11],
                    params[12],
                    params[13],
                    params[14],
                    params[15],
                    params[16],
                )
                self.rowcount = 1
        elif normalized.startswith("insert into usage_events"):
            request_id = str(params[3])
            if request_id not in self.connection.usage:
                self.connection.usage[request_id] = (
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[7],
                    params[8],
                    params[9],
                )
                self.rowcount = 1
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class _Connection:
    def __init__(self) -> None:
        self.evidence: dict[str, tuple[object, ...]] = {}
        self.usage: dict[str, tuple[object, ...]] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self


def _pair() -> tuple[ProviderEvidence, UsageEvent]:
    event = UsageEvent(
        tenant_id=TENANT,
        project_id=PROJECT,
        job_id=JOB,
        provider_request_id="request-1",
        operation="render",
        units={"characters": 12},
        currency="USD",
        estimated_cost=Decimal("0.12"),
        reconciled_cost=None,
        created_at=OCCURRED,
    )
    evidence = ProviderEvidence(
        operation="render",
        provider="elevenlabs",
        request_id="request-1",
        model="eleven-multilingual-v2",
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        usage={"characters": 12},
        currency="USD",
        estimated_cost=Decimal("0.12"),
        reconciled_cost=None,
        latency_ms=42,
        retry_count=0,
        occurred_at=OCCURRED,
        evidence_kind="provider-live",
    )
    return evidence, event


@given("a recording PostgreSQL evidence ledger connection")
def recording_connection(context) -> None:
    context.values["connection"] = _Connection()
    context.values["ledger"] = PostgresEvidenceLedger()
    context.values["evidence"], context.values["event"] = _pair()


@when("I record the same provider evidence pair twice")
def record_twice(context) -> None:
    connection = context.values["connection"]
    ledger = context.values["ledger"]
    evidence = context.values["evidence"]
    event = context.values["event"]
    context.values["first"] = ledger.record(connection, evidence, event)
    context.values["second"] = ledger.record(connection, evidence, event)


@then("the evidence and usage are inserted once and replay identically")
def pair_replays(context) -> None:
    assert context.values["first"] == (
        context.values["evidence"],
        context.values["event"],
    )
    assert context.values["second"] == context.values["first"]
    inserts = [
        statement
        for statement in context.values["connection"].statements
        if statement[0].lstrip().casefold().startswith("insert into")
    ]
    assert len(inserts) == 2


@when("the usage row exists without provider evidence")
def partial_pair(context) -> None:
    connection = context.values["connection"]
    ledger = context.values["ledger"]
    evidence = context.values["evidence"]
    event = context.values["event"]
    connection.usage[event.provider_request_id] = (
        str(event.project_id),
        str(event.job_id),
        event.provider_request_id,
        event.operation,
        json.dumps(dict(event.units)),
        event.currency,
        format(event.estimated_cost, "f"),
        None,
        event.created_at,
    )
    with pytest.raises(ValueError, match="together"):
        ledger.record(connection, evidence, event)


@then("recording the pair rejects the inconsistent state")
def partial_pair_rejected(context) -> None:
    assert context.values["connection"].evidence == {}
