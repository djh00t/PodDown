"""Tests for the short-lived PostgreSQL usage-ledger adapter."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from poddown.persistence import UsageEvent, UsageNotFound
from poddown.postgres_ledger import (
    PostgresLedgerConflict,
    PostgresUsageLedger,
)
from poddown.postgres_persistence import (
    PostgresPersistenceConnection,
    PostgresPersistenceCursor,
)
from poddown.providers.contracts import ProviderEvidence

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
OTHER_TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-444444444444")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
JOB = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
OTHER_JOB = UUID("018f2c8b-7b46-7cc5-b2e1-555555555555")
OCCURRED = datetime(2026, 8, 14, tzinfo=UTC)


class _Cursor(PostgresPersistenceCursor):
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.rows: list[Sequence[object]] = []
        self.rowcount = 0

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        normalized = " ".join(sql.split()).casefold()
        self.connection.statements.append((sql, params))
        self.rows = []
        self.rowcount = 0
        if normalized.startswith("select set_config"):
            return self
        if normalized.startswith("select project_id, job_id, request_id"):
            tenant_id, request_id = str(params[0]), str(params[1])
            row = self.connection.evidence.get((tenant_id, request_id))
            if row is not None:
                self.rows = [row]
            return self
        if (
            normalized.startswith("select project_id, job_id, provider_request_id")
            and "order by" not in normalized
        ):
            tenant_id, request_id = str(params[0]), str(params[1])
            row = self.connection.usage.get((tenant_id, request_id))
            if row is not None:
                self.rows = [row]
            return self
        if normalized.startswith("select") and "from usage_events" in normalized:
            tenant_id, job_id = str(params[0]), str(params[1])
            rows = [
                row
                for (row_tenant, _), row in self.connection.usage.items()
                if row_tenant == tenant_id and str(row[1]) == job_id
            ]
            self.rows = sorted(rows, key=lambda row: (row[8], str(row[2])))
            return self
        if normalized.startswith("insert into provider_evidence"):
            tenant_id, request_id = str(params[0]), str(params[3])
            key = (tenant_id, request_id)
            if key not in self.connection.evidence:
                self.connection.evidence[key] = (
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
            return self
        if normalized.startswith("insert into usage_events"):
            tenant_id, request_id = str(params[0]), str(params[3])
            key = (tenant_id, request_id)
            if key not in self.connection.usage:
                self.connection.usage[key] = (
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
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> Sequence[object] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[Sequence[object]]:
        return self.rows


class _Connection(PostgresPersistenceConnection):
    def __init__(self) -> None:
        self.evidence: dict[tuple[str, str], tuple[object, ...]] = {}
        self.usage: dict[tuple[str, str], tuple[object, ...]] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.transaction_count = 0
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self) -> Iterator[_Connection]:
        self.transaction_count += 1
        yield self

    def close(self) -> None:
        self.closed = True


def _connection_factory(connection: _Connection) -> Callable[[], _Connection]:
    def factory() -> _Connection:
        return connection

    return factory


def _pair(
    request_id: str = "request-1",
    *,
    output_sha256: str = "b" * 64,
    occurred_at: datetime = OCCURRED,
    job_id: UUID = JOB,
) -> tuple[ProviderEvidence, UsageEvent]:
    event = UsageEvent(
        tenant_id=TENANT,
        project_id=PROJECT,
        job_id=job_id,
        provider_request_id=request_id,
        operation="render",
        units={"characters": 12},
        currency="USD",
        estimated_cost=Decimal("0.12"),
        reconciled_cost=None,
        created_at=occurred_at,
    )
    evidence = ProviderEvidence(
        operation="render",
        provider="elevenlabs",
        request_id=request_id,
        model="eleven-multilingual-v2",
        input_sha256="a" * 64,
        output_sha256=output_sha256,
        usage={"characters": 12},
        currency="USD",
        estimated_cost=Decimal("0.12"),
        reconciled_cost=None,
        latency_ms=42,
        retry_count=0,
        occurred_at=occurred_at,
        evidence_kind="provider-live",
    )
    return evidence, event


def _usage_row(event: UsageEvent) -> tuple[object, ...]:
    return (
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


def test_postgres_usage_ledger_replays_atomic_evidence_and_usage_pair() -> None:
    connection = _Connection()
    ledger = PostgresUsageLedger(_connection_factory(connection))
    evidence, event = _pair()

    first = ledger.record_evidence(evidence, event)
    second = ledger.record_evidence(evidence, event)

    assert first == (evidence, event)
    assert second == first
    assert connection.transaction_count == 2
    assert connection.closed is True
    assert (
        len(
            [
                statement
                for statement in connection.statements
                if statement[0].lstrip().casefold().startswith("insert into")
            ]
        )
        == 2
    )


def test_postgres_usage_ledger_rejects_conflicting_provider_replay() -> None:
    connection = _Connection()
    ledger = PostgresUsageLedger(_connection_factory(connection))
    evidence, event = _pair()
    ledger.record_evidence(evidence, event)

    conflicting_evidence, _ = _pair(output_sha256="c" * 64)
    with pytest.raises(PostgresLedgerConflict):
        ledger.record_evidence(conflicting_evidence, event)


def test_postgres_usage_ledger_records_and_reads_usage_without_evidence() -> None:
    connection = _Connection()
    ledger = PostgresUsageLedger(_connection_factory(connection))
    event = _pair()[1]

    assert ledger.record(event) == event
    assert ledger.record(event) == event
    assert ledger.get(TENANT, event.provider_request_id) == event

    conflicting = replace(event, estimated_cost=Decimal("0.13"))
    with pytest.raises(PostgresLedgerConflict):
        ledger.record(conflicting)


def test_postgres_usage_ledger_reads_provider_evidence_and_rejects_missing() -> None:
    connection = _Connection()
    ledger = PostgresUsageLedger(_connection_factory(connection))
    evidence, event = _pair()
    ledger.record_evidence(evidence, event)

    assert ledger.get_evidence(TENANT, evidence.request_id) == evidence
    with pytest.raises(UsageNotFound):
        ledger.get_evidence(TENANT, "missing-request")


def test_postgres_usage_ledger_reads_scoped_usage_in_stable_order() -> None:
    connection = _Connection()
    ledger = PostgresUsageLedger(_connection_factory(connection))
    first = _pair(
        request_id="request-b",
        occurred_at=OCCURRED + timedelta(seconds=2),
    )[1]
    second = _pair(
        request_id="request-a",
        occurred_at=OCCURRED + timedelta(seconds=1),
    )[1]
    foreign = _pair(
        request_id="request-foreign",
        occurred_at=OCCURRED,
        job_id=OTHER_JOB,
    )[1]
    connection.usage[(str(TENANT), first.provider_request_id)] = _usage_row(first)
    connection.usage[(str(TENANT), second.provider_request_id)] = _usage_row(second)
    connection.usage[(str(OTHER_TENANT), foreign.provider_request_id)] = _usage_row(
        foreign
    )

    values = ledger.list_for_job(TENANT, JOB)

    assert values == (second, first)
    with pytest.raises(UsageNotFound):
        ledger.get(TENANT, "missing-request")
