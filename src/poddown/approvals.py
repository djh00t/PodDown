"""Durable, scoped, one-time publication approvals."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol
from uuid import UUID

ApprovalOperation = Literal["publish", "update", "delete"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_INIT_LOCK = Lock()


class ApprovalError(ValueError):
    """Base error for approval validation and persistence failures."""


class ApprovalNotUsable(ApprovalError):
    """An approval is absent, expired, mismatched, or outside caller scope."""


class ApprovalAlreadyConsumed(ApprovalNotUsable):
    """A valid approval was already consumed by an earlier operation."""


class ApprovalConflict(ApprovalError, RuntimeError):
    """An approval identifier was reused with different immutable data."""


def _uuid7(value: UUID, field: str) -> UUID:
    if not isinstance(value, UUID) or value.version != 7:
        raise ApprovalError(f"{field} must be a UUIDv7")
    return value


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApprovalError(f"{field} must be non-empty")
    return value


def _digest(value: str, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ApprovalError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ApprovalError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PublicationApproval:
    """Immutable approval record with auditable issue and consumption times."""

    tenant_id: UUID
    project_id: UUID
    approval_id: UUID
    episode_id: UUID
    target_id: str
    operation: ApprovalOperation
    package_sha256: str
    actor_id: str
    nonce_sha256: str
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        _uuid7(self.tenant_id, "tenant_id")
        _uuid7(self.project_id, "project_id")
        _uuid7(self.approval_id, "approval_id")
        _uuid7(self.episode_id, "episode_id")
        if _TARGET.fullmatch(self.target_id) is None:
            raise ApprovalError("target_id is invalid")
        if self.operation not in {"publish", "update", "delete"}:
            raise ApprovalError("approval operation is invalid")
        _digest(self.package_sha256, "package_sha256")
        _text(self.actor_id, "actor_id")
        _digest(self.nonce_sha256, "nonce_sha256")
        issued_at = _timestamp(self.issued_at, "issued_at")
        expires_at = _timestamp(self.expires_at, "expires_at")
        if expires_at <= issued_at:
            raise ApprovalError("expires_at must be after issued_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        if self.consumed_at is not None:
            consumed_at = _timestamp(self.consumed_at, "consumed_at")
            if consumed_at < issued_at:
                raise ApprovalError("consumed_at cannot precede issued_at")
            object.__setattr__(self, "consumed_at", consumed_at)

    def to_dict(self) -> dict[str, object]:
        """Return auditable metadata; no raw approval nonce is stored or returned."""
        return {
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "approval_id": str(self.approval_id),
            "episode_id": str(self.episode_id),
            "target_id": self.target_id,
            "operation": self.operation,
            "package_sha256": self.package_sha256,
            "actor_id": self.actor_id,
            "nonce_sha256": self.nonce_sha256,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "consumed_at": (
                self.consumed_at.isoformat() if self.consumed_at is not None else None
            ),
        }


class ApprovalRepository:
    """Protocol-like base for durable approval implementations."""

    def issue(self, approval: PublicationApproval) -> PublicationApproval:
        """Persist one immutable approval or replay an identical issue."""
        raise NotImplementedError

    def consume(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        approval_id: UUID,
        target_id: str,
        operation: ApprovalOperation,
        package_sha256: str,
        actor_id: str,
        now: datetime,
    ) -> PublicationApproval:
        """Consume one scoped, unexpired approval exactly once."""
        raise NotImplementedError


class PostgresApprovalCursor(Protocol):
    """Minimal DB-API cursor required by the PostgreSQL approval adapter."""

    rowcount: int

    def execute(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> PostgresApprovalCursor:
        """Execute one parameterized statement."""

    def fetchone(self) -> Sequence[object] | None:
        """Fetch one row from the latest query."""


class PostgresApprovalConnection(Protocol):
    """Caller-owned PostgreSQL connection with transaction context."""

    def cursor(self) -> PostgresApprovalCursor:
        """Return a cursor for the current transaction."""

    def transaction(self) -> AbstractContextManager[object]:
        """Return a transaction context manager."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS publication_approvals (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    nonce_sha256 TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    PRIMARY KEY (tenant_id, approval_id)
);
CREATE INDEX IF NOT EXISTS publication_approvals_scope_idx
    ON publication_approvals (tenant_id, project_id, episode_id, target_id);
"""


def _database_path(value: Path | str) -> Path:
    if not isinstance(value, (Path, str)) or str(value) == ":memory:":
        raise ValueError("a durable filesystem database path is required")
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _iso(value: datetime) -> str:
    return _timestamp(value, "timestamp").isoformat()


def _parse_iso(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ApprovalError(f"persisted {field} is invalid")
    try:
        return _timestamp(datetime.fromisoformat(value), field)
    except ValueError as error:
        raise ApprovalError(f"persisted {field} is invalid") from error


def _initialize(path: Path) -> None:
    with _INIT_LOCK:
        connection = sqlite3.connect(path, timeout=10.0)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)
            connection.commit()
        finally:
            connection.close()


def _record(row: sqlite3.Row) -> PublicationApproval:
    try:
        return PublicationApproval(
            tenant_id=UUID(row["tenant_id"]),
            project_id=UUID(row["project_id"]),
            approval_id=UUID(row["approval_id"]),
            episode_id=UUID(row["episode_id"]),
            target_id=row["target_id"],
            operation=row["operation"],
            package_sha256=row["package_sha256"],
            actor_id=row["actor_id"],
            nonce_sha256=row["nonce_sha256"],
            issued_at=_parse_iso(row["issued_at"], "issued_at"),
            expires_at=_parse_iso(row["expires_at"], "expires_at"),
            consumed_at=(
                _parse_iso(row["consumed_at"], "consumed_at")
                if row["consumed_at"] is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError, ApprovalError) as error:
        if isinstance(error, ApprovalError):
            raise
        raise ApprovalError("persisted approval is invalid") from error


def _same_immutable(left: PublicationApproval, right: PublicationApproval) -> bool:
    return left.to_dict() | {"consumed_at": None} == right.to_dict() | {
        "consumed_at": None
    }


def _postgres_uuid(value: object, field: str) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as error:
        raise ApprovalError(f"persisted {field} is invalid") from error
    return _uuid7(parsed, field)


def _postgres_timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return _timestamp(value, field)
    if isinstance(value, str):
        try:
            return _timestamp(datetime.fromisoformat(value), field)
        except ValueError as error:
            raise ApprovalError(f"persisted {field} is invalid") from error
    raise ApprovalError(f"persisted {field} is invalid")


def _postgres_record(
    tenant_id: UUID, approval_id: UUID, row: Sequence[object]
) -> PublicationApproval:
    """Decode the tenant-scoped PostgreSQL approval projection fail closed."""
    if len(row) != 10:
        raise ApprovalError("persisted approval row has an unexpected shape")
    (
        project_id,
        episode_id,
        target_id,
        operation,
        package_sha256,
        actor_id,
        nonce_sha256,
        issued_at,
        expires_at,
        consumed_at,
    ) = row
    try:
        return PublicationApproval(
            tenant_id=tenant_id,
            project_id=_postgres_uuid(project_id, "project_id"),
            approval_id=approval_id,
            episode_id=_postgres_uuid(episode_id, "episode_id"),
            target_id=str(target_id),
            operation=operation,  # type: ignore[arg-type]
            package_sha256=str(package_sha256),
            actor_id=str(actor_id),
            nonce_sha256=str(nonce_sha256),
            issued_at=_postgres_timestamp(issued_at, "issued_at"),
            expires_at=_postgres_timestamp(expires_at, "expires_at"),
            consumed_at=(
                _postgres_timestamp(consumed_at, "consumed_at")
                if consumed_at is not None
                else None
            ),
        )
    except (TypeError, ValueError, ApprovalError) as error:
        if isinstance(error, ApprovalError):
            raise
        raise ApprovalError("persisted approval is invalid") from error


def _set_postgres_tenant(cursor: PostgresApprovalCursor, tenant_id: UUID) -> None:
    cursor.execute(
        "SELECT set_config('poddown.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def _close_postgres_connection(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


class PostgresApprovalRepository(ApprovalRepository):
    """Tenant-scoped PostgreSQL approval repository with caller transactions."""

    _SELECT = (
        "SELECT project_id, episode_id, target_id, operation, package_sha256, "
        "actor_id, nonce_sha256, issued_at, expires_at, consumed_at "
        "FROM publication_approvals "
        "WHERE tenant_id = %s AND approval_id = %s"
    )

    def __init__(
        self, connection_factory: Callable[[], PostgresApprovalConnection]
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def _get(
        self,
        cursor: PostgresApprovalCursor,
        tenant_id: UUID,
        approval_id: UUID,
        *,
        for_update: bool = False,
    ) -> PublicationApproval | None:
        cursor.execute(
            self._SELECT + (" FOR UPDATE" if for_update else ""),
            (str(tenant_id), str(approval_id)),
        )
        row = cursor.fetchone()
        return (
            _postgres_record(tenant_id, approval_id, row) if row is not None else None
        )

    def issue(self, approval: PublicationApproval) -> PublicationApproval:
        """Insert or replay one immutable approval in a PostgreSQL transaction."""
        if not isinstance(approval, PublicationApproval):
            raise ApprovalError("approval must be a PublicationApproval")
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_postgres_tenant(cursor, approval.tenant_id)
                existing = self._get(cursor, approval.tenant_id, approval.approval_id)
                if existing is not None:
                    if not _same_immutable(existing, approval):
                        raise ApprovalConflict("approval identity conflicts")
                    return existing
                cursor.execute(
                    """INSERT INTO publication_approvals (
                    tenant_id, project_id, approval_id, episode_id, target_id,
                    operation, package_sha256, actor_id, nonce_sha256, issued_at,
                    expires_at, consumed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (tenant_id, approval_id) DO NOTHING""",
                    (
                        str(approval.tenant_id),
                        str(approval.project_id),
                        str(approval.approval_id),
                        str(approval.episode_id),
                        approval.target_id,
                        approval.operation,
                        approval.package_sha256,
                        approval.actor_id,
                        approval.nonce_sha256,
                        approval.issued_at,
                        approval.expires_at,
                    ),
                )
                inserted = self._get(cursor, approval.tenant_id, approval.approval_id)
                if inserted is None:
                    raise ApprovalError("approval insert was not observable")
                if not _same_immutable(inserted, approval):
                    raise ApprovalConflict("approval identity conflicts")
                return inserted
        finally:
            _close_postgres_connection(connection)

    def consume(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        approval_id: UUID,
        target_id: str,
        operation: ApprovalOperation,
        package_sha256: str,
        actor_id: str,
        now: datetime,
    ) -> PublicationApproval:
        """Consume one scoped approval with a row lock and conditional update."""
        for value, field in (
            (tenant_id, "tenant_id"),
            (project_id, "project_id"),
            (episode_id, "episode_id"),
            (approval_id, "approval_id"),
        ):
            _uuid7(value, field)
        _text(target_id, "target_id")
        if operation not in {"publish", "update", "delete"}:
            raise ApprovalNotUsable("approval operation is invalid")
        _digest(package_sha256, "package_sha256")
        _text(actor_id, "actor_id")
        consumed_at = _timestamp(now, "now")
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_postgres_tenant(cursor, tenant_id)
                existing = self._get(cursor, tenant_id, approval_id, for_update=True)
                if existing is None:
                    raise ApprovalNotUsable("approval is not usable")
                if existing.consumed_at is not None:
                    raise ApprovalAlreadyConsumed("approval was already consumed")
                if (
                    existing.project_id != project_id
                    or existing.episode_id != episode_id
                    or existing.target_id != target_id
                    or existing.operation != operation
                    or existing.package_sha256 != package_sha256
                    or existing.actor_id != actor_id
                    or consumed_at >= existing.expires_at
                ):
                    raise ApprovalNotUsable("approval is not usable")
                cursor.execute(
                    """UPDATE publication_approvals SET consumed_at = %s
                   WHERE tenant_id = %s AND approval_id = %s
                   AND consumed_at IS NULL""",
                    (consumed_at, str(tenant_id), str(approval_id)),
                )
                if cursor.rowcount != 1:
                    raise ApprovalAlreadyConsumed("approval was already consumed")
                return replace(existing, consumed_at=consumed_at)
        finally:
            _close_postgres_connection(connection)


class SQLiteApprovalRepository(ApprovalRepository):
    """Restart-safe SQLite implementation for local and test deployments."""

    def __init__(self, database: Path | str) -> None:
        self._database = _database_path(database)
        _initialize(self._database)

    def issue(self, approval: PublicationApproval) -> PublicationApproval:
        if not isinstance(approval, PublicationApproval):
            raise ApprovalError("approval must be a PublicationApproval")
        connection = sqlite3.connect(self._database, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM publication_approvals "
                "WHERE tenant_id = ? AND approval_id = ?",
                (str(approval.tenant_id), str(approval.approval_id)),
            ).fetchone()
            if row is not None:
                existing = _record(row)
                if not _same_immutable(existing, approval):
                    raise ApprovalConflict("approval identity conflicts")
                connection.commit()
                return existing
            connection.execute(
                """INSERT INTO publication_approvals (
                    tenant_id, project_id, approval_id, episode_id, target_id,
                    operation, package_sha256, actor_id, nonce_sha256, issued_at,
                    expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    str(approval.tenant_id),
                    str(approval.project_id),
                    str(approval.approval_id),
                    str(approval.episode_id),
                    approval.target_id,
                    approval.operation,
                    approval.package_sha256,
                    approval.actor_id,
                    approval.nonce_sha256,
                    _iso(approval.issued_at),
                    _iso(approval.expires_at),
                ),
            )
            connection.commit()
            return approval
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def consume(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        approval_id: UUID,
        target_id: str,
        operation: ApprovalOperation,
        package_sha256: str,
        actor_id: str,
        now: datetime,
    ) -> PublicationApproval:
        """Consume with a conditional update inside one SQLite transaction."""
        for value, field in (
            (tenant_id, "tenant_id"),
            (project_id, "project_id"),
            (episode_id, "episode_id"),
            (approval_id, "approval_id"),
        ):
            _uuid7(value, field)
        _text(target_id, "target_id")
        if operation not in {"publish", "update", "delete"}:
            raise ApprovalNotUsable("approval operation is invalid")
        _digest(package_sha256, "package_sha256")
        _text(actor_id, "actor_id")
        consumed_at = _timestamp(now, "now")
        connection = sqlite3.connect(self._database, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM publication_approvals "
                "WHERE tenant_id = ? AND approval_id = ?",
                (str(tenant_id), str(approval_id)),
            ).fetchone()
            if row is None:
                raise ApprovalNotUsable("approval is not usable")
            existing = _record(row)
            if existing.consumed_at is not None:
                raise ApprovalAlreadyConsumed("approval was already consumed")
            if (
                existing.project_id != project_id
                or existing.episode_id != episode_id
                or existing.target_id != target_id
                or existing.operation != operation
                or existing.package_sha256 != package_sha256
                or existing.actor_id != actor_id
                or consumed_at >= existing.expires_at
            ):
                raise ApprovalNotUsable("approval is not usable")
            result = connection.execute(
                """UPDATE publication_approvals SET consumed_at = ?
                   WHERE tenant_id = ? AND approval_id = ? AND consumed_at IS NULL""",
                (_iso(consumed_at), str(tenant_id), str(approval_id)),
            )
            if result.rowcount != 1:
                raise ApprovalAlreadyConsumed("approval was already consumed")
            connection.commit()
            return replace(existing, consumed_at=consumed_at)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


__all__ = [
    "ApprovalAlreadyConsumed",
    "ApprovalConflict",
    "ApprovalError",
    "ApprovalNotUsable",
    "ApprovalOperation",
    "ApprovalRepository",
    "PostgresApprovalConnection",
    "PostgresApprovalCursor",
    "PostgresApprovalRepository",
    "PublicationApproval",
    "SQLiteApprovalRepository",
]
