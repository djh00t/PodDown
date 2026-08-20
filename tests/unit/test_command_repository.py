"""Unit coverage for durable command-job repository behavior."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from poddown.command_repository import (
    CommandJobRepository,
    CommandPersistenceError,
    InvalidCommandTransition,
)


class _CursorFailureConnection:
    poddown_database_kind = "test"

    def __init__(self) -> None:
        self.rollback_count = 0

    def cursor(self) -> None:
        raise RuntimeError("driver cursor details")

    def rollback(self) -> None:
        self.rollback_count += 1


class _ConflictRereadCursor:
    def __init__(self, failure: str, *, reread: bool = False) -> None:
        self._failure = failure
        self._reread = reread
        self.rowcount = 1
        self._execute_count = 0

    def execute(self, operation: str, parameters: object = ()) -> None:
        self._execute_count += 1
        if self._execute_count == 2:
            raise sqlite3.IntegrityError("driver conflict details")
        if self._reread and self._failure == "query":
            raise RuntimeError("driver reread details")

    def fetchone(self) -> None:
        return None


class _ConflictRereadConnection:
    poddown_database_kind = "test"

    def __init__(self, failure: str) -> None:
        self._failure = failure
        self._initial_cursor = _ConflictRereadCursor(failure)
        self._cursor_count = 0
        self.rollback_count = 0

    def cursor(self) -> _ConflictRereadCursor:
        self._cursor_count += 1
        if self._cursor_count == 2 and self._failure == "cursor":
            raise RuntimeError("driver reread details")
        return (
            self._initial_cursor
            if self._cursor_count == 1
            else _ConflictRereadCursor(self._failure, reread=True)
        )

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        self.rollback_count += 1


def _record_command(repository: CommandJobRepository) -> None:
    repository.record_or_replay(
        tenant_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e1"),
        project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e3"),
        episode_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e4"),
        episode_version_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e5"),
        job_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e6"),
        workflow_id="workflow-1",
        content_sha256="a" * 64,
        request_sha256="b" * 64,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


def _transition_command(repository: CommandJobRepository) -> None:
    repository.transition(
        tenant_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e1"),
        project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e3"),
        episode_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e4"),
        episode_version_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e5"),
        job_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e6"),
        state="running",
    )


@pytest.mark.parametrize("operation", [_record_command, _transition_command])
def test_command_repository_hides_connection_factory_failures(
    operation: object,
) -> None:
    """Return a typed persistence error when connection creation fails."""
    repository = CommandJobRepository(
        lambda: (_ for _ in ()).throw(RuntimeError("driver connection details"))
    )

    with pytest.raises(
        CommandPersistenceError, match="command job persistence failed"
    ) as error:
        operation(repository)  # type: ignore[operator]

    assert "driver connection details" not in str(error.value)


@pytest.mark.parametrize("operation", [_record_command, _transition_command])
def test_command_repository_hides_cursor_acquisition_failures(
    operation: object,
) -> None:
    """Roll back and return a typed persistence error when cursor creation fails."""
    connection = _CursorFailureConnection()
    repository = CommandJobRepository(lambda: connection)  # type: ignore[arg-type]

    with pytest.raises(
        CommandPersistenceError, match="command job persistence failed"
    ) as error:
        operation(repository)  # type: ignore[operator]

    assert connection.rollback_count == 1
    assert "driver cursor details" not in str(error.value)


@pytest.mark.parametrize("failure", ["cursor", "query"])
def test_command_replay_conflict_reread_hides_driver_failures(failure: str) -> None:
    """A failed conflict reread returns a safe typed error after rollback."""
    connection = _ConflictRereadConnection(failure)
    repository = CommandJobRepository(lambda: connection)

    with pytest.raises(
        CommandPersistenceError, match="command job persistence failed"
    ) as error:
        _record_command(repository)

    assert connection.rollback_count >= 1
    assert "driver reread details" not in str(error.value)


def test_command_repository_closes_factory_connection_after_operation(
    tmp_path: Path,
) -> None:
    """Close each one-shot DB-API connection after the repository operation."""
    database = tmp_path / "command-repository.sqlite3"
    bootstrap = sqlite3.connect(database)
    bootstrap.executescript(
        """CREATE TABLE render_jobs (
        id TEXT PRIMARY KEY, tenant_id TEXT, project_id TEXT, episode_id TEXT,
        episode_version_id TEXT, workflow_id TEXT, content_sha256 TEXT,
        request_sha256 TEXT, status TEXT, attempt INTEGER, created_at TEXT,
        UNIQUE (tenant_id, workflow_id), UNIQUE (tenant_id, id, request_sha256));"""
    )
    bootstrap.commit()
    bootstrap.close()
    created: list[sqlite3.Connection] = []

    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        created.append(connection)
        return connection

    _record_command(CommandJobRepository(factory))

    assert len(created) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        created[0].execute("SELECT 1")


def test_command_job_transitions_only_from_its_current_state(tmp_path: Path) -> None:
    """Reject a completed command job from returning to a running state."""
    database = tmp_path / "command-transitions.sqlite3"
    bootstrap = sqlite3.connect(database)
    bootstrap.executescript(
        """CREATE TABLE render_jobs (
        id TEXT PRIMARY KEY, tenant_id TEXT, project_id TEXT, episode_id TEXT,
        episode_version_id TEXT, workflow_id TEXT, content_sha256 TEXT,
        request_sha256 TEXT, status TEXT, attempt INTEGER, created_at TEXT,
        UNIQUE (tenant_id, workflow_id), UNIQUE (tenant_id, id, request_sha256));"""
    )
    bootstrap.commit()
    bootstrap.close()

    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        return connection

    repository = CommandJobRepository(factory)
    tenant_id = UUID("0198d59c-b420-70ce-ae46-786eeeb387e1")
    job = repository.record_or_replay(
        tenant_id=tenant_id,
        project_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e3"),
        episode_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e4"),
        episode_version_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e5"),
        job_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e6"),
        workflow_id="workflow-1",
        content_sha256="a" * 64,
        request_sha256="b" * 64,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    scope = {
        "tenant_id": tenant_id,
        "project_id": UUID("0198d59c-b420-70ce-ae46-786eeeb387e3"),
        "episode_id": UUID("0198d59c-b420-70ce-ae46-786eeeb387e4"),
        "episode_version_id": UUID("0198d59c-b420-70ce-ae46-786eeeb387e5"),
        "job_id": job.job_id,
    }
    repository.transition(**scope, state="running")
    repository.transition(**scope, state="completed")
    with pytest.raises(InvalidCommandTransition):
        repository.transition(**scope, state="running")
