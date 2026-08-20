"""Durable DB-API repository seam for tenant-scoped render command jobs."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from poddown.tenant_context import tenant_transaction

CommandState = Literal["queued", "running", "completed", "failed", "cancelled"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RepositoryCursor(Protocol):
    rowcount: int

    def execute(self, operation: str, parameters: Sequence[object] = ()) -> object: ...
    def fetchone(self) -> Sequence[object] | None: ...


class RepositoryConnection(Protocol):
    def cursor(self) -> RepositoryCursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class CommandRepositoryError(ValueError):
    """Base error for command job repository failures."""


class CommandReplayConflict(CommandRepositoryError):
    """A command identity was rebound to different immutable job evidence."""


class CommandJobNotFound(CommandRepositoryError):
    """A command job is absent from the caller's tenant scope."""


class InvalidCommandTransition(CommandRepositoryError):
    """A command state transition is not permitted."""


class CommandPersistenceError(CommandRepositoryError):
    """A command job could not be safely persisted or replayed."""


@dataclass(frozen=True, slots=True)
class CommandJob:
    """Durable receipt and workflow identity for one render command."""

    tenant_id: UUID
    project_id: UUID
    episode_id: UUID
    episode_version_id: UUID
    job_id: UUID
    workflow_id: str
    content_sha256: str
    request_sha256: str
    state: CommandState
    created_at: datetime

    def immutable_identity(self) -> tuple[object, ...]:
        return (
            self.tenant_id,
            self.project_id,
            self.episode_id,
            self.episode_version_id,
            self.job_id,
            self.workflow_id,
            self.content_sha256,
            self.request_sha256,
            self.created_at,
        )


_TRANSITIONS: dict[CommandState, frozenset[CommandState]] = {
    "queued": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def _timestamp(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _validate_uuid7(*values: UUID) -> None:
    if any(not isinstance(value, UUID) or value.version != 7 for value in values):
        raise ValueError("command job UUIDv7 identity is invalid")


def _command_job_from_row(row: Sequence[object]) -> CommandJob:
    try:
        state = str(row[8])
        if state not in _TRANSITIONS:
            raise ValueError("persisted command state is invalid")
        job = CommandJob(
            UUID(str(row[0])),
            UUID(str(row[1])),
            UUID(str(row[2])),
            UUID(str(row[3])),
            UUID(str(row[4])),
            str(row[5]),
            str(row[6]),
            str(row[7]),
            state,
            datetime.fromisoformat(str(row[9])),
        )
        _validate_uuid7(
            job.tenant_id,
            job.project_id,
            job.episode_id,
            job.episode_version_id,
            job.job_id,
        )
        if (
            not job.workflow_id
            or _SHA256.fullmatch(job.content_sha256) is None
            or _SHA256.fullmatch(job.request_sha256) is None
        ):
            raise ValueError("persisted command job identity is invalid")
        _timestamp(job.created_at)
        return job
    except (IndexError, TypeError, ValueError) as error:
        raise CommandRepositoryError("persisted command job is invalid") from error


_SELECT = (
    "SELECT tenant_id, project_id, episode_id, episode_version_id, id, workflow_id, "
    "content_sha256, request_sha256, status, created_at FROM render_jobs "
    "WHERE tenant_id = ? AND id = ?"
)


def _placeholder(connection: RepositoryConnection) -> str:
    return "?" if isinstance(connection, sqlite3.Connection) else "%s"


def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


class CommandJobRepository:
    """Persist and safely transition render command jobs through DB-API."""

    def __init__(self, connection_factory: Callable[[], RepositoryConnection]) -> None:
        self._connection_factory = connection_factory

    def record_or_replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        episode_version_id: UUID,
        job_id: UUID,
        workflow_id: str,
        content_sha256: str,
        request_sha256: str,
        created_at: datetime,
    ) -> CommandJob:
        _validate_uuid7(tenant_id, project_id, episode_id, episode_version_id, job_id)
        if (
            not isinstance(workflow_id, str)
            or not workflow_id.strip()
            or _SHA256.fullmatch(content_sha256) is None
            or _SHA256.fullmatch(request_sha256) is None
        ):
            raise ValueError("command job identity is invalid")
        created_at_text = _timestamp(created_at)
        expected = CommandJob(
            tenant_id,
            project_id,
            episode_id,
            episode_version_id,
            job_id,
            workflow_id,
            content_sha256,
            request_sha256,
            "queued",
            created_at,
        )
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise CommandPersistenceError("command job persistence failed") from error
        placeholder = _placeholder(connection)
        try:
            with tenant_transaction(connection, tenant_id) as cursor:
                cursor.execute(
                    _SELECT.replace("?", placeholder), (str(tenant_id), str(job_id))
                )
                row = cursor.fetchone()
                if row is not None:
                    stored = _command_job_from_row(row)
                    if stored.immutable_identity() != expected.immutable_identity():
                        raise CommandReplayConflict(
                            "command replay conflicts with receipt"
                        )
                    return stored
                cursor.execute(
                    "INSERT INTO render_jobs (id, tenant_id, project_id, episode_id, "
                    "episode_version_id, workflow_id, content_sha256, request_sha256, "
                    "status, attempt, created_at) VALUES "
                    f"({', '.join((placeholder,) * 11)})",
                    (
                        str(job_id),
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        str(episode_version_id),
                        workflow_id,
                        content_sha256,
                        request_sha256,
                        "queued",
                        1,
                        created_at_text,
                    ),
                )
                return expected
        except sqlite3.IntegrityError as error:
            try:
                with tenant_transaction(connection, tenant_id) as cursor:
                    cursor.execute(
                        _SELECT.replace("?", placeholder), (str(tenant_id), str(job_id))
                    )
                    row = cursor.fetchone()
            except Exception as reread_error:
                raise CommandPersistenceError(
                    "command job persistence failed"
                ) from reread_error
            if row is None:
                raise CommandReplayConflict(
                    "command replay conflicts with receipt"
                ) from error
            stored = _command_job_from_row(row)
            if stored.immutable_identity() != expected.immutable_identity():
                raise CommandReplayConflict(
                    "command replay conflicts with receipt"
                ) from error
            return stored
        except sqlite3.DatabaseError as error:
            raise CommandPersistenceError("command job persistence failed") from error
        except CommandRepositoryError:
            raise
        except Exception as error:
            raise CommandPersistenceError("command job persistence failed") from error
        finally:
            _close(connection)

    def transition(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        episode_version_id: UUID,
        job_id: UUID,
        state: CommandState,
    ) -> CommandJob:
        _validate_uuid7(tenant_id, project_id, episode_id, episode_version_id, job_id)
        if state not in _TRANSITIONS:
            raise InvalidCommandTransition("command state transition is invalid")
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise CommandPersistenceError("command job persistence failed") from error
        placeholder = _placeholder(connection)
        predicate = (
            str(tenant_id),
            str(project_id),
            str(episode_id),
            str(episode_version_id),
            str(job_id),
        )
        try:
            with tenant_transaction(connection, tenant_id) as cursor:
                cursor.execute(
                    (
                        _SELECT + " AND project_id = ? AND episode_id = ? AND "
                        "episode_version_id = ?"
                    ).replace("?", placeholder),
                    (
                        str(tenant_id),
                        str(job_id),
                        str(project_id),
                        str(episode_id),
                        str(episode_version_id),
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise CommandJobNotFound("command job is unavailable")
                stored = _command_job_from_row(row)
                if state not in _TRANSITIONS[stored.state]:
                    raise InvalidCommandTransition(
                        "command state transition is invalid"
                    )
                cursor.execute(
                    (
                        "UPDATE render_jobs SET status = ? WHERE tenant_id = ? AND "
                        "project_id = ? AND episode_id = ? AND episode_version_id = ? "
                        "AND id = ? AND status = ?"
                    ).replace("?", placeholder),
                    (state, *predicate, stored.state),
                )
                if cursor.rowcount != 1:
                    raise InvalidCommandTransition(
                        "command state transition is invalid"
                    )
                return CommandJob(
                    stored.tenant_id,
                    stored.project_id,
                    stored.episode_id,
                    stored.episode_version_id,
                    stored.job_id,
                    stored.workflow_id,
                    stored.content_sha256,
                    stored.request_sha256,
                    state,
                    stored.created_at,
                )
        except sqlite3.DatabaseError as error:
            raise CommandPersistenceError("command job persistence failed") from error
        except CommandRepositoryError:
            raise
        except Exception as error:
            raise CommandPersistenceError("command job persistence failed") from error
        finally:
            _close(connection)
