"""Durable, one-time publication approval storage."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

PublicationOperation = Literal["publish", "update", "delete"]
_OPERATIONS = frozenset({"publish", "update", "delete"})
_MISMATCH_MESSAGE = "publication approval does not match request"


class PublicationApprovalError(ValueError):
    """Base error for publication approval validation and storage."""


class PublicationApprovalNotFound(PublicationApprovalError):
    """The approval is absent."""


class PublicationApprovalMismatch(PublicationApprovalNotFound):
    """The request does not match the complete approval identity."""


class PublicationApprovalConsumed(PublicationApprovalError):
    """The approval has already been consumed."""


class PublicationApprovalExpired(PublicationApprovalError):
    """The approval cannot be consumed outside its validity window."""


class PublicationApprovalStorageError(PublicationApprovalError):
    """The local approval repository could not complete a storage operation."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS publication_approvals (
    approval_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    nonce_sha256 TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
"""


def _require_uuid7(name: str, value: object) -> UUID:
    if not isinstance(value, UUID):
        raise PublicationApprovalError(f"{name} must be UUIDv7")
    try:
        version = value.version
    except Exception as error:
        raise PublicationApprovalError(f"{name} must be UUIDv7") from error
    if version != 7:
        raise PublicationApprovalError(f"{name} must be UUIDv7")
    return value


def _require_text(name: str, value: object) -> str:
    if type(value) is not str:
        raise PublicationApprovalError(f"{name} must be a non-empty string")
    try:
        if not value.strip():
            raise PublicationApprovalError(f"{name} must be a non-empty string")
    except PublicationApprovalError:
        raise
    except Exception as error:
        raise PublicationApprovalError(f"{name} must be a non-empty string") from error
    return value


def _require_operation(value: object) -> PublicationOperation:
    if type(value) is not str or value not in _OPERATIONS:
        raise PublicationApprovalError("publication operation is invalid")
    return cast(PublicationOperation, value)


def _require_nonce_hash(value: object) -> str:
    if type(value) is not str or len(value) != 64:
        raise PublicationApprovalError("approval nonce hash is invalid")
    if any(character not in "0123456789abcdef" for character in value):
        raise PublicationApprovalError("approval nonce hash is invalid")
    return value


def _normalize_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise PublicationApprovalError(f"{name} must be timezone-aware")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise PublicationApprovalError(f"{name} must be timezone-aware")
        normalized = datetime.fromtimestamp(value.timestamp(), tz=UTC)
    except PublicationApprovalError:
        raise
    except Exception as error:
        raise PublicationApprovalError(f"{name} must be timezone-aware") from error
    if not isinstance(normalized, datetime):
        raise PublicationApprovalError(f"{name} must be timezone-aware")
    return normalized


def _timestamp(name: str, value: object) -> str:
    return _normalize_datetime(name, value).isoformat()


def _parse_timestamp(name: str, value: object) -> datetime:
    if type(value) is not str:
        raise PublicationApprovalError(f"{name} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except Exception as error:
        raise PublicationApprovalError(f"{name} is invalid") from error
    return _normalize_datetime(name, parsed)


def _nonce_sha256(value: object) -> str:
    nonce = _require_text("approval nonce", value)
    try:
        return hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    except Exception as error:
        raise PublicationApprovalError("approval nonce is invalid") from error


def _persisted_uuid(name: str, value: object) -> UUID:
    if type(value) is not str:
        raise PublicationApprovalError(f"persisted {name} is invalid")
    try:
        parsed = UUID(value)
    except Exception as error:
        raise PublicationApprovalError(f"persisted {name} is invalid") from error
    try:
        return _require_uuid7(name, parsed)
    except PublicationApprovalError as error:
        raise PublicationApprovalError(f"persisted {name} is invalid") from error


@dataclass(frozen=True, slots=True)
class PublicationApproval:
    """Immutable approval bound to one scoped publication operation."""

    approval_id: UUID
    tenant_id: UUID
    project_id: UUID
    episode_id: UUID
    operation: PublicationOperation
    actor_id: str
    nonce_sha256: str
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        approval_id = _require_uuid7("approval_id", self.approval_id)
        tenant_id = _require_uuid7("tenant_id", self.tenant_id)
        project_id = _require_uuid7("project_id", self.project_id)
        episode_id = _require_uuid7("episode_id", self.episode_id)
        operation = _require_operation(self.operation)
        actor_id = _require_text("actor_id", self.actor_id)
        nonce_sha256 = _require_nonce_hash(self.nonce_sha256)
        issued_at = _normalize_datetime("issued_at", self.issued_at)
        expires_at = _normalize_datetime("expires_at", self.expires_at)
        consumed_at = (
            _normalize_datetime("consumed_at", self.consumed_at)
            if self.consumed_at is not None
            else None
        )
        if expires_at <= issued_at:
            raise PublicationApprovalError("approval expiry must follow issue time")
        if consumed_at is not None and not issued_at <= consumed_at <= expires_at:
            raise PublicationApprovalError("approval consumption is outside validity")
        object.__setattr__(self, "approval_id", approval_id)
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "episode_id", episode_id)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "nonce_sha256", nonce_sha256)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "consumed_at", consumed_at)


def _validated_approval(value: object) -> PublicationApproval:
    if not isinstance(value, PublicationApproval):
        raise PublicationApprovalError("publication approval value is invalid")
    try:
        return PublicationApproval(
            approval_id=value.approval_id,
            tenant_id=value.tenant_id,
            project_id=value.project_id,
            episode_id=value.episode_id,
            operation=value.operation,
            actor_id=value.actor_id,
            nonce_sha256=value.nonce_sha256,
            issued_at=value.issued_at,
            expires_at=value.expires_at,
            consumed_at=value.consumed_at,
        )
    except PublicationApprovalError:
        raise
    except Exception as error:
        raise PublicationApprovalError(
            "publication approval value is invalid"
        ) from error


def _row_value(row: sqlite3.Row, name: str) -> object:
    try:
        return row[name]
    except Exception as error:
        raise PublicationApprovalError(
            "persisted publication approval is invalid"
        ) from error


def _approval_from_row(row: sqlite3.Row) -> PublicationApproval:
    try:
        consumed_value = _row_value(row, "consumed_at")
        return PublicationApproval(
            approval_id=_persisted_uuid("approval_id", _row_value(row, "approval_id")),
            tenant_id=_persisted_uuid("tenant_id", _row_value(row, "tenant_id")),
            project_id=_persisted_uuid("project_id", _row_value(row, "project_id")),
            episode_id=_persisted_uuid("episode_id", _row_value(row, "episode_id")),
            operation=_require_operation(_row_value(row, "operation")),
            actor_id=_require_text("actor_id", _row_value(row, "actor_id")),
            nonce_sha256=_require_nonce_hash(_row_value(row, "nonce_sha256")),
            issued_at=_parse_timestamp(
                "persisted issued_at", _row_value(row, "issued_at")
            ),
            expires_at=_parse_timestamp(
                "persisted expires_at", _row_value(row, "expires_at")
            ),
            consumed_at=(
                _parse_timestamp("persisted consumed_at", consumed_value)
                if consumed_value is not None
                else None
            ),
        )
    except PublicationApprovalError:
        raise
    except Exception as error:
        raise PublicationApprovalError(
            "persisted publication approval is invalid"
        ) from error


def _row_matches_identity(
    row: sqlite3.Row,
    *,
    approval_id: UUID,
    tenant_id: UUID,
    project_id: UUID,
    episode_id: UUID,
    operation: PublicationOperation,
    actor_id: str,
    nonce_sha256: str,
) -> bool:
    return (
        _persisted_uuid("approval_id", _row_value(row, "approval_id")) == approval_id
        and _persisted_uuid("tenant_id", _row_value(row, "tenant_id")) == tenant_id
        and _persisted_uuid("project_id", _row_value(row, "project_id")) == project_id
        and _persisted_uuid("episode_id", _row_value(row, "episode_id")) == episode_id
        and _require_operation(_row_value(row, "operation")) == operation
        and _require_text("actor_id", _row_value(row, "actor_id")) == actor_id
        and _require_nonce_hash(_row_value(row, "nonce_sha256")) == nonce_sha256
    )


class SQLitePublicationApprovalRepository:
    """SQLite repository with atomic, scope-bound approval consumption."""

    def __init__(self, database: Path | str) -> None:
        if not isinstance(database, (Path, str)):
            raise PublicationApprovalError(
                "publication approval database path is invalid"
            )
        try:
            path = Path(database)
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as error:
            raise PublicationApprovalError(
                "publication approval database path is invalid"
            ) from error
        self._database = path
        try:
            with self._connection() as connection:
                connection.executescript(_SCHEMA)
        except PublicationApprovalError:
            raise
        except Exception as error:
            raise PublicationApprovalStorageError(
                "publication approval repository is unavailable"
            ) from error

    def record(self, approval: PublicationApproval) -> None:
        """Persist an issued approval using only its nonce hash."""
        validated = _validated_approval(approval)
        try:
            with self._connection() as connection:
                connection.execute(
                    """INSERT INTO publication_approvals (
                        approval_id, tenant_id, project_id, episode_id, operation,
                        actor_id, nonce_sha256, issued_at, expires_at, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(validated.approval_id),
                        str(validated.tenant_id),
                        str(validated.project_id),
                        str(validated.episode_id),
                        validated.operation,
                        validated.actor_id,
                        validated.nonce_sha256,
                        _timestamp("issued_at", validated.issued_at),
                        _timestamp("expires_at", validated.expires_at),
                        _timestamp("consumed_at", validated.consumed_at)
                        if validated.consumed_at is not None
                        else None,
                    ),
                )
        except PublicationApprovalError:
            raise
        except Exception as error:
            raise PublicationApprovalStorageError(
                "publication approval could not be recorded"
            ) from error

    def consume(
        self,
        *,
        approval_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        operation: PublicationOperation,
        actor_id: str,
        nonce: str,
        occurred_at: datetime,
    ) -> PublicationApproval:
        """Atomically consume one matching approval within its validity window."""
        approval_id = _require_uuid7("approval_id", approval_id)
        tenant_id = _require_uuid7("tenant_id", tenant_id)
        project_id = _require_uuid7("project_id", project_id)
        episode_id = _require_uuid7("episode_id", episode_id)
        operation = _require_operation(operation)
        actor_id = _require_text("actor_id", actor_id)
        nonce_sha256 = _nonce_sha256(nonce)
        timestamp = _normalize_datetime("occurred_at", occurred_at)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM publication_approvals WHERE approval_id = ?",
                    (str(approval_id),),
                ).fetchone()
                if row is None:
                    raise PublicationApprovalMismatch(_MISMATCH_MESSAGE)
                if not _row_matches_identity(
                    row,
                    approval_id=approval_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    episode_id=episode_id,
                    operation=operation,
                    actor_id=actor_id,
                    nonce_sha256=nonce_sha256,
                ):
                    raise PublicationApprovalMismatch(_MISMATCH_MESSAGE)
                approval = _approval_from_row(row)
                if approval.consumed_at is not None:
                    raise PublicationApprovalConsumed(
                        "publication approval is consumed"
                    )
                if timestamp < approval.issued_at or timestamp > approval.expires_at:
                    raise PublicationApprovalExpired("publication approval is expired")
                result = connection.execute(
                    """UPDATE publication_approvals
                       SET consumed_at = ?
                       WHERE approval_id = ? AND tenant_id = ? AND project_id = ?
                         AND episode_id = ? AND operation = ? AND actor_id = ?
                         AND nonce_sha256 = ? AND consumed_at IS NULL
                         AND issued_at <= ? AND expires_at >= ?""",
                    (
                        _timestamp("occurred_at", timestamp),
                        str(approval_id),
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        operation,
                        actor_id,
                        nonce_sha256,
                        _timestamp("occurred_at", timestamp),
                        _timestamp("occurred_at", timestamp),
                    ),
                )
                if result.rowcount != 1:
                    raise PublicationApprovalMismatch(_MISMATCH_MESSAGE)
        except PublicationApprovalError:
            raise
        except Exception as error:
            raise PublicationApprovalStorageError(
                "publication approval could not be consumed"
            ) from error
        return PublicationApproval(
            approval_id=approval.approval_id,
            tenant_id=approval.tenant_id,
            project_id=approval.project_id,
            episode_id=approval.episode_id,
            operation=approval.operation,
            actor_id=approval.actor_id,
            nonce_sha256=approval.nonce_sha256,
            issued_at=approval.issued_at,
            expires_at=approval.expires_at,
            consumed_at=timestamp,
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(
                self._database, isolation_level=None, timeout=5.0
            )
        except Exception as error:
            raise PublicationApprovalStorageError(
                "publication approval repository is unavailable"
            ) from error
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()
