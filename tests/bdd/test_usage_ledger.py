"""BDD bindings for the durable provider usage ledger."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from pytest_bdd import given, scenarios, then, when

from poddown.persistence import UsageEvent
from poddown.usage_repository import UsageLedgerRepository

scenarios("../features/usage_ledger.feature")


@given("a durable usage event with estimated and reconciled costs")
def durable_usage_event(context, tmp_path: Path) -> None:
    """Create the repository schema and immutable evidence fixture."""
    database = tmp_path / "usage-ledger.sqlite3"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE usage_events (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            publication_id TEXT,
            project_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_request_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            unit_type TEXT NOT NULL,
            units TEXT NOT NULL,
            usage TEXT NOT NULL,
            currency TEXT NOT NULL,
            estimated_cost TEXT NOT NULL,
            reconciled_cost TEXT,
            occurred_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tenant_id, provider, provider_request_id)
        )"""
    )
    connection.execute(
        """CREATE TABLE render_jobs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            UNIQUE (tenant_id, id, project_id)
        )"""
    )
    connection.execute(
        "INSERT INTO render_jobs (id, tenant_id, project_id) VALUES (?, ?, ?)",
        (
            "0198d59c-b420-70ce-ae46-786eeeb387e6",
            "0198d59c-b420-70ce-ae46-786eeeb387e1",
            "0198d59c-b420-70ce-ae46-786eeeb387e3",
        ),
    )
    connection.commit()
    connection.close()

    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        return connection

    context.values["repository"] = UsageLedgerRepository(factory)
    context.values["event"] = UsageEvent(
        tenant_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e1"),
        project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e3"),
        job_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e6"),
        provider_request_id="request-123",
        operation="render",
        units={"input_units": 12, "output_units": 5},
        currency="USD",
        estimated_cost=Decimal("0.0123000000000000007"),
        reconciled_cost=Decimal("0.0121000000000000003"),
        created_at=datetime(2026, 8, 12, 12, 30, tzinfo=UTC),
    )


@when("the same provider request is recorded twice")
def replay_provider_request(context) -> None:
    """Record the real event twice through the durable repository boundary."""
    repository = context.values["repository"]
    event = context.values["event"]
    context.values["recorded"] = (
        repository.record_or_replay(provider="elevenlabs", event=event),
        repository.record_or_replay(provider="elevenlabs", event=event),
    )


@then("one tenant-scoped usage event retains its exact Decimal costs")
def usage_event_retains_exact_costs(context) -> None:
    """Assert replay identity and plain-string Decimal persistence."""
    first, second = context.values["recorded"]

    assert first == second
    assert first.estimated_cost == Decimal("0.0123000000000000007")
    assert first.reconciled_cost == Decimal("0.0121000000000000003")
