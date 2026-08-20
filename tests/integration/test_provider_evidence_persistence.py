"""Integration coverage for atomic normalized provider evidence persistence."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from poddown.persistence import (
    SQLiteUsageLedger,
    UsageConflict,
    UsageEvent,
    UsageNotFound,
)
from poddown.providers.contracts import ProviderEvidence

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
JOB_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b15")
OCCURRED_AT = datetime(2026, 8, 14, 4, 0, tzinfo=UTC)


def evidence(*, output_sha256: str = "b" * 64) -> ProviderEvidence:
    """Build normalized evidence without raw provider payload fields."""
    return ProviderEvidence(
        operation="render",
        provider="elevenlabs",
        request_id="render-request-1",
        model="eleven-multilingual-v2",
        input_sha256="a" * 64,
        output_sha256=output_sha256,
        usage={"input_units": 12, "output_units": 48},
        currency="USD",
        estimated_cost=Decimal("0.0040"),
        reconciled_cost=Decimal("0.0035"),
        latency_ms=420,
        retry_count=1,
        occurred_at=OCCURRED_AT,
        evidence_kind="provider-live",
    )


def usage_event(value: ProviderEvidence) -> UsageEvent:
    """Bind usage and cost to the exact evidence request identity."""
    return UsageEvent(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        job_id=JOB_ID,
        provider_request_id=value.request_id,
        operation=value.operation,
        units=value.usage,
        currency=value.currency,
        estimated_cost=value.estimated_cost,
        reconciled_cost=value.reconciled_cost,
        created_at=value.occurred_at,
    )


def test_evidence_and_usage_replay_once_without_raw_payload_columns(
    tmp_path: Path,
) -> None:
    """A restart returns one immutable evidence/cost pair."""
    database = tmp_path / "evidence.sqlite3"
    ledger = SQLiteUsageLedger(database)
    value = evidence()
    event = usage_event(value)

    assert ledger.record_evidence(value, event) == (value, event)
    restarted = SQLiteUsageLedger(database)
    assert restarted.get_evidence(TENANT_ID, value.request_id) == value
    assert restarted.record_evidence(value, event) == (value, event)
    assert restarted.list_for_job(TENANT_ID, JOB_ID) == (event,)

    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_evidence)")
        }
    finally:
        connection.close()
    assert {"raw_audio", "raw_payload", "authorization", "secret"}.isdisjoint(columns)
    assert {
        "request_id",
        "input_sha256",
        "output_sha256",
        "estimated_cost",
        "latency_ms",
        "retry_count",
        "evidence_kind",
    }.issubset(columns)


def test_evidence_conflict_does_not_add_a_second_usage_event(tmp_path: Path) -> None:
    """A request ID cannot be rebound to a different immutable digest."""
    ledger = SQLiteUsageLedger(tmp_path / "evidence.sqlite3")
    value = evidence()
    event = usage_event(value)
    ledger.record_evidence(value, event)
    conflicting = evidence(output_sha256="c" * 64)

    with pytest.raises(UsageConflict):
        ledger.record_evidence(conflicting, usage_event(conflicting))

    assert ledger.list_for_job(TENANT_ID, JOB_ID) == (event,)


def test_evidence_insert_rolls_back_when_usage_insert_fails(tmp_path: Path) -> None:
    """A failed cost insert cannot leave an orphaned evidence row."""
    database = tmp_path / "evidence.sqlite3"
    ledger = SQLiteUsageLedger(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """CREATE TRIGGER reject_usage_event
            BEFORE INSERT ON usage_events
            BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"""
        )
        connection.commit()
    finally:
        connection.close()
    value = evidence()

    with pytest.raises(UsageConflict):
        ledger.record_evidence(value, usage_event(value))

    with pytest.raises(UsageNotFound):
        ledger.get_evidence(TENANT_ID, value.request_id)
    assert ledger.list_for_job(TENANT_ID, JOB_ID) == ()
