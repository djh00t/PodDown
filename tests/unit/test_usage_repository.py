"""Unit coverage for durable PostgreSQL-style usage ledger behavior."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from poddown.persistence import UsageEvent
from poddown.usage_repository import (
    UsageEventUnavailable,
    UsageLedgerRepository,
    UsageReplayConflict,
)


def _repository(tmp_path: Path) -> tuple[UsageLedgerRepository, Path]:
    database = tmp_path / "usage-repository.sqlite3"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE usage_events (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, publication_id TEXT,
            project_id TEXT NOT NULL,
            job_id TEXT NOT NULL, provider TEXT NOT NULL,
            provider_request_id TEXT NOT NULL, operation TEXT NOT NULL,
            unit_type TEXT NOT NULL, units TEXT NOT NULL, usage TEXT NOT NULL,
            currency TEXT NOT NULL, estimated_cost TEXT NOT NULL,
            reconciled_cost TEXT, occurred_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tenant_id, provider, provider_request_id)
        )"""
    )
    connection.execute(
        """CREATE TABLE render_jobs (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
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

    return UsageLedgerRepository(factory), database


def _event(*, estimated_cost: Decimal = Decimal("0.012300")) -> UsageEvent:
    return UsageEvent(
        tenant_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e1"),
        project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e3"),
        job_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e6"),
        provider_request_id="request-123",
        operation="render",
        units={"input_units": 12, "output_units": 5},
        currency="USD",
        estimated_cost=estimated_cost,
        reconciled_cost=Decimal("0.012100"),
        created_at=datetime(2026, 8, 12, 12, 30, tzinfo=UTC),
    )


def test_provider_request_replay_returns_the_original_exact_usage_event(
    tmp_path: Path,
) -> None:
    """Catch duplicate handling that changes accepted Decimal evidence."""
    repository, _ = _repository(tmp_path)

    stored = repository.record_or_replay(provider="elevenlabs", event=_event())
    replayed = repository.record_or_replay(provider="elevenlabs", event=_event())

    assert replayed == stored
    assert stored.to_dict()["estimated_cost"] == "0.012300"
    assert stored.to_dict()["reconciled_cost"] == "0.012100"


def test_provider_request_replay_rejects_different_immutable_cost_evidence(
    tmp_path: Path,
) -> None:
    """Catch a provider request ID being rebound to a different cost amount."""
    repository, _ = _repository(tmp_path)
    repository.record_or_replay(provider="elevenlabs", event=_event())

    with pytest.raises(UsageReplayConflict, match="usage replay conflicts"):
        repository.record_or_replay(
            provider="elevenlabs", event=_event(estimated_cost=Decimal("0.099900"))
        )


def test_job_listing_requires_the_matching_tenant_project_and_job_scope(
    tmp_path: Path,
) -> None:
    """Catch usage reads that leak a provider request across project/job scope."""
    repository, _ = _repository(tmp_path)
    stored = repository.record_or_replay(provider="elevenlabs", event=_event())

    assert repository.list_for_job(
        tenant_id=stored.tenant_id,
        project_id=stored.project_id,
        job_id=stored.job_id,
    ) == (stored,)
    assert (
        repository.list_for_job(
            tenant_id=stored.tenant_id,
            project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e4"),
            job_id=stored.job_id,
        )
        == ()
    )


def test_record_rejects_a_job_owned_by_another_project(tmp_path: Path) -> None:
    """Catch a tenant-valid job ID being recorded against another project."""
    repository, database = _repository(tmp_path)
    cross_project_event = replace(
        _event(), project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e4")
    )

    with pytest.raises(UsageEventUnavailable, match="job is unavailable"):
        repository.record_or_replay(provider="elevenlabs", event=cross_project_event)

    connection = sqlite3.connect(database)
    try:
        assert (
            connection.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 0
        )
    finally:
        connection.close()


class _PostgresCursor:
    """PostgreSQL-shaped cursor recording repository SQL without a live database."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self._row: tuple[int] | None = None

    def execute(self, operation: str, parameters=()) -> None:
        self.executed.append((operation, tuple(parameters)))
        self._row = (1,) if "SELECT 1 FROM render_jobs" in operation else None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return ()


class _PostgresConnection:
    """PostgreSQL-shaped connection exposing psycopg's parameter convention."""

    poddown_database_kind = "test"

    def __init__(self) -> None:
        self.cursor_value = _PostgresCursor()
        self.closed = False

    def cursor(self) -> _PostgresCursor:
        return self.cursor_value

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


_PostgresConnection.__module__ = "psycopg.connection"


class IntegrityError(Exception):
    """Stand-in for psycopg's driver-specific DB-API integrity error."""


IntegrityError.__module__ = "psycopg.errors"


def test_postgresql_insert_uses_production_parameter_syntax() -> None:
    """Catch SQL that cannot write D12's production-shaped usage_events table."""
    connection = _PostgresConnection()
    repository = UsageLedgerRepository(lambda: connection)

    repository.record_or_replay(provider="elevenlabs", event=_event())

    statements = "\n".join(
        operation for operation, _ in connection.cursor_value.executed
    )
    assert "SELECT 1 FROM render_jobs" in statements
    assert "INSERT INTO usage_events" in statements
    assert "publication_id" not in statements
    assert "%s" in statements
    assert "?" not in statements
    assert connection.closed


class _PostgresUniqueConflictCursor:
    """PostgreSQL-shaped cursor that reports a concurrent matching insert."""

    def __init__(self, event: UsageEvent) -> None:
        self._event = event
        self._row: tuple[object, ...] | None = None
        self.insert_attempts = 0

    def execute(self, operation: str, parameters=()) -> None:
        del parameters
        if "SELECT 1 FROM render_jobs" in operation:
            self._row = (1,)
        elif operation.startswith("INSERT INTO usage_events"):
            self.insert_attempts += 1
            raise IntegrityError("duplicate provider request")
        elif operation.startswith("SELECT tenant_id"):
            self._row = (
                (
                    str(self._event.tenant_id),
                    str(self._event.project_id),
                    str(self._event.job_id),
                    "elevenlabs",
                    self._event.provider_request_id,
                    self._event.operation,
                    '{"input_units":12,"output_units":5}',
                    self._event.currency,
                    format(self._event.estimated_cost, "f"),
                    format(self._event.reconciled_cost, "f"),
                    self._event.created_at.isoformat(),
                )
                if self.insert_attempts
                else None
            )

    def fetchone(self):
        return self._row

    def fetchall(self):
        return ()


class _PostgresUniqueConflictConnection:
    """PostgreSQL-shaped connection for a provider-request uniqueness race."""

    poddown_database_kind = "test"

    def __init__(self, event: UsageEvent) -> None:
        self.cursor_value = _PostgresUniqueConflictCursor(event)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _PostgresUniqueConflictCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_postgresql_provider_request_uniqueness_replays_matching_event() -> None:
    """Return the matching immutable event after a PostgreSQL insert race."""
    event = _event()
    connection = _PostgresUniqueConflictConnection(event)
    repository = UsageLedgerRepository(lambda: connection)

    assert repository.record_or_replay(provider="elevenlabs", event=event) == event
    assert connection.cursor_value.insert_attempts == 1
    assert connection.commits == 1
    assert connection.rollbacks == 1
