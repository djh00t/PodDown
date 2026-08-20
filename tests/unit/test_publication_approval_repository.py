"""Unit coverage for publication approval repository scope failures."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from poddown.publication_approval_repository import (
    ApprovalPersistenceError,
    ApprovalUnavailable,
    PublicationApprovalRepository,
)


class _CursorFailureConnection:
    poddown_database_kind = "test"

    def __init__(self) -> None:
        self.rollback_count = 0

    def cursor(self) -> None:
        raise RuntimeError("driver cursor details")

    def rollback(self) -> None:
        self.rollback_count += 1


class _ApprovalRereadCursor:
    def __init__(self, failure: str, *, conflicts: bool, reread: bool = False) -> None:
        self._failure = failure
        self._conflicts = conflicts
        self._reread = reread
        self._execute_count = 0
        self.rowcount = 1

    def execute(self, operation: str, parameters: object = ()) -> None:
        self._execute_count += 1
        if self._conflicts and self._execute_count == 2:
            raise sqlite3.IntegrityError("driver conflict details")
        if (
            self._reread or (not self._conflicts and self._execute_count == 2)
        ) and self._failure == "query":
            raise RuntimeError("driver reread details")

    def fetchone(self) -> None:
        return None


class _ApprovalRereadConnection:
    poddown_database_kind = "test"

    def __init__(self, failure: str, *, conflicts: bool) -> None:
        self._failure = failure
        self._conflicts = conflicts
        self._initial_cursor = _ApprovalRereadCursor(failure, conflicts=conflicts)
        self._cursor_count = 0
        self.rollback_count = 0

    def cursor(self) -> _ApprovalRereadCursor:
        self._cursor_count += 1
        if self._cursor_count == 2 and self._failure == "cursor":
            raise RuntimeError("driver reread details")
        return (
            self._initial_cursor
            if self._cursor_count == 1
            else _ApprovalRereadCursor(self._failure, conflicts=False, reread=True)
        )

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        self.rollback_count += 1


def _record_approval(repository: PublicationApprovalRepository) -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    repository.record(
        approval_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e8"),
        tenant_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e1"),
        project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e3"),
        episode_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e4"),
        publication_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e7"),
        operation="publish",
        approval_reference="approval-1",
        actor_id="publisher@example.test",
        nonce_sha256="c" * 64,
        approved_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def _consume_approval(repository: PublicationApprovalRepository) -> None:
    repository.consume(
        approval_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e8"),
        tenant_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e1"),
        project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e3"),
        episode_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e4"),
        publication_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e7"),
        operation="publish",
        actor_id="publisher@example.test",
        nonce_sha256="c" * 64,
        consumed_at=datetime(2026, 8, 12, 0, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize("operation", [_record_approval, _consume_approval])
def test_approval_repository_hides_connection_factory_failures(
    operation: object,
) -> None:
    """Return a typed persistence error when connection creation fails."""
    repository = PublicationApprovalRepository(
        lambda: (_ for _ in ()).throw(RuntimeError("driver connection details"))
    )

    with pytest.raises(
        ApprovalPersistenceError, match="publication approval persistence failed"
    ) as error:
        operation(repository)  # type: ignore[operator]

    assert "driver connection details" not in str(error.value)


@pytest.mark.parametrize("operation", [_record_approval, _consume_approval])
def test_approval_repository_hides_cursor_acquisition_failures(
    operation: object,
) -> None:
    """Roll back and return a typed persistence error when cursor creation fails."""
    connection = _CursorFailureConnection()
    repository = PublicationApprovalRepository(lambda: connection)  # type: ignore[arg-type]

    with pytest.raises(
        ApprovalPersistenceError, match="publication approval persistence failed"
    ) as error:
        operation(repository)  # type: ignore[operator]

    assert connection.rollback_count == 1
    assert "driver cursor details" not in str(error.value)


@pytest.mark.parametrize("failure", ["cursor", "query"])
def test_approval_record_conflict_reread_hides_driver_failures(failure: str) -> None:
    """A failed approval conflict reread returns a safe typed error after rollback."""
    connection = _ApprovalRereadConnection(failure, conflicts=True)
    repository = PublicationApprovalRepository(lambda: connection)

    with pytest.raises(
        ApprovalPersistenceError, match="publication approval persistence failed"
    ) as error:
        _record_approval(repository)

    assert connection.rollback_count >= 1
    assert "driver reread details" not in str(error.value)


def test_approval_consume_reread_hides_driver_query_failures() -> None:
    """A failed consumption reread returns a safe typed error after rollback."""
    connection = _ApprovalRereadConnection("query", conflicts=False)
    repository = PublicationApprovalRepository(lambda: connection)

    with pytest.raises(
        ApprovalPersistenceError, match="publication approval persistence failed"
    ) as error:
        _consume_approval(repository)

    assert connection.rollback_count >= 1
    assert "driver reread details" not in str(error.value)


def test_approval_consumption_hides_a_different_episode(tmp_path: Path) -> None:
    """Treat a cross-episode approval lookup as unavailable."""
    database = tmp_path / "approval-repository.sqlite3"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """CREATE TABLE publications (
        id TEXT PRIMARY KEY, tenant_id TEXT, project_id TEXT, episode_id TEXT
        );
        CREATE TABLE publication_approvals (
        id TEXT PRIMARY KEY, tenant_id TEXT, publication_id TEXT,
        operation TEXT, approval_reference TEXT, approved_at TEXT, expires_at TEXT,
        consumed_at TEXT, created_at TEXT, actor_id TEXT, nonce_sha256 TEXT
        );"""
    )
    tenant_id = UUID("0198d59c-b420-70ce-ae46-786eeeb387e1")
    project_id = UUID("0198d59c-b420-70ce-ae46-786eeeb387e3")
    episode_id = UUID("0198d59c-b420-70ce-ae46-786eeeb387e4")
    publication_id = UUID("0198d59c-b420-70ce-ae46-786eeeb387e7")
    approval_id = UUID("0198d59c-b420-70ce-ae46-786eeeb387e8")
    now = datetime(2026, 8, 12, tzinfo=UTC)
    connection.execute(
        "INSERT INTO publications VALUES (?, ?, ?, ?)",
        (str(publication_id), str(tenant_id), str(project_id), str(episode_id)),
    )
    connection.commit()
    connection.commit()
    connection.close()

    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        return connection

    repository = PublicationApprovalRepository(factory)
    repository.record(
        approval_id=approval_id,
        tenant_id=tenant_id,
        project_id=project_id,
        episode_id=episode_id,
        publication_id=publication_id,
        operation="publish",
        approval_reference="approval-1",
        actor_id="publisher@example.test",
        nonce_sha256="c" * 64,
        approved_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    with pytest.raises(ApprovalUnavailable):
        repository.consume(
            approval_id=approval_id,
            tenant_id=tenant_id,
            project_id=project_id,
            episode_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e5"),
            publication_id=publication_id,
            operation="publish",
            actor_id="publisher@example.test",
            nonce_sha256="c" * 64,
            consumed_at=now + timedelta(minutes=1),
        )
