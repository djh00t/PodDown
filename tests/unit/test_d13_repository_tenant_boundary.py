"""Unit coverage for D13 repository transaction-boundary adoption.

These explicitly test-marked DB-API fakes verify transaction ordering only.
They do not provide evidence that a live PostgreSQL role enforces RLS.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from poddown.command_repository import CommandJobRepository
from poddown.persistence import UsageEvent
from poddown.publication_approval_repository import PublicationApprovalRepository
from poddown.usage_repository import UsageLedgerRepository

TENANT_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e1")
PROJECT_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e3")
EPISODE_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e4")
EPISODE_VERSION_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e5")
JOB_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e6")
PUBLICATION_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e7")
APPROVAL_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e8")


class _Cursor:
    def __init__(self, calls: list[str], repository: str) -> None:
        self._calls = calls
        self._repository = repository
        self._row: tuple[int] | None = None
        self.rowcount = 1

    def execute(self, operation: str, parameters: Sequence[object] = ()) -> None:
        del parameters
        self._calls.append(operation)
        self._row = (
            (1,)
            if self._repository == "usage" and "SELECT 1 FROM render_jobs" in operation
            else None
        )

    def fetchone(self) -> tuple[int] | None:
        return self._row

    def fetchall(self) -> tuple[()]:
        return ()


class _TestConnection:
    """Explicit test fake with PostgreSQL parameter syntax, never RLS evidence."""

    poddown_database_kind = "test"

    def __init__(self, repository: str) -> None:
        self.calls: list[str] = []
        self._cursor = _Cursor(self.calls, repository)
        self.closed = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.calls.append("COMMIT")

    def rollback(self) -> None:
        self.calls.append("ROLLBACK")

    def close(self) -> None:
        self.closed = True


_TestConnection.__module__ = "psycopg.connection"


def test_usage_repository_starts_the_shared_tenant_boundary_before_usage_sql() -> None:
    """Catch usage SQL running before its tenant transaction begins."""
    connection = _TestConnection("usage")
    repository = UsageLedgerRepository(lambda: connection)
    event = UsageEvent(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        job_id=JOB_ID,
        provider_request_id="request-123",
        operation="render",
        units={"input_units": 1},
        currency="USD",
        estimated_cost=Decimal("0.01"),
        reconciled_cost=None,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    repository.record_or_replay(provider="provider", event=event)

    assert connection.calls[:2] == [
        "BEGIN",
        "SELECT 1 FROM render_jobs WHERE tenant_id = %s AND project_id = %s "
        "AND id = %s",
    ]


def test_command_repository_starts_the_shared_tenant_boundary_before_command_sql() -> (
    None
):
    """Catch render-command SQL running before its tenant transaction begins."""
    connection = _TestConnection("command")
    repository = CommandJobRepository(lambda: connection)

    repository.record_or_replay(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        episode_version_id=EPISODE_VERSION_ID,
        job_id=JOB_ID,
        workflow_id="workflow-1",
        content_sha256="a" * 64,
        request_sha256="b" * 64,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert connection.calls[0] == "BEGIN"
    assert connection.calls[1].startswith("SELECT tenant_id")


def test_approval_repository_starts_boundary_before_sql() -> None:
    """Catch publication-approval SQL running before its tenant transaction begins."""
    connection = _TestConnection("approval")
    repository = PublicationApprovalRepository(lambda: connection)
    now = datetime(2026, 8, 12, tzinfo=UTC)

    repository.record(
        approval_id=APPROVAL_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        publication_id=PUBLICATION_ID,
        operation="publish",
        approval_reference="approval-1",
        actor_id="publisher@example.test",
        nonce_sha256="c" * 64,
        approved_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    assert connection.calls[0] == "BEGIN"
    assert connection.calls[1].startswith("SELECT approval.id")
    assert connection.closed
