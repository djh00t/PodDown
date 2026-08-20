"""Durable, provider-free publication attempt and receipt repository."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast
from uuid import UUID

from poddown.publishing import (
    DisclosurePolicy,
    PublicationAuthorization,
    PublicationReceipt,
    PublicationTarget,
    PublishingValidationError,
    sanitize_publication_error,
)

PublicationAttemptStatus = Literal["pending", "succeeded", "failed", "uncertain"]
ConnectionFactory = Callable[[bool], AbstractContextManager[sqlite3.Connection]]


class PublicationRepositoryError(ValueError):
    """Base error for durable publication attempt storage."""


class PublicationIdentityConflict(PublicationRepositoryError):
    """An idempotency key is bound to different immutable publication inputs."""


class PublicationOutcomeUncertain(PublicationRepositoryError):
    """A previous provider outcome is unknown and must not be retried blindly."""


class PublicationAttemptNotFound(PublicationRepositoryError):
    """A publication attempt is absent or no longer pending."""


@dataclass(frozen=True, slots=True)
class PublicationAttemptRequest:
    """Immutable identity required before one provider publication attempt."""

    tenant_id: UUID
    project_id: UUID
    episode_version_id: str
    target_id: str
    idempotency_key: str
    package_sha256: str
    target_snapshot: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.tenant_id.version != 7 or self.project_id.version != 7:
            raise PublicationRepositoryError("publication scope must use UUIDv7")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.episode_version_id,
                self.target_id,
                self.idempotency_key,
                self.package_sha256,
            )
        ):
            raise PublicationRepositoryError("publication identity is incomplete")
        if len(self.package_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.package_sha256
        ):
            raise PublicationRepositoryError("publication package checksum is invalid")
        object.__setattr__(
            self, "target_snapshot", _freeze_target(self.target_snapshot)
        )

    @classmethod
    def from_receipt(
        cls, receipt: PublicationReceipt, target: PublicationTarget
    ) -> PublicationAttemptRequest:
        if (
            receipt.tenant_id != target.tenant_id
            or receipt.project_id != target.project_id
            or receipt.target_id != target.target_id
        ):
            raise PublicationIdentityConflict("receipt and target scopes differ")
        return cls(
            tenant_id=receipt.tenant_id,
            project_id=receipt.project_id,
            episode_version_id=receipt.episode_version_id,
            target_id=receipt.target_id,
            idempotency_key=receipt.idempotency_key,
            package_sha256=receipt.package_sha256,
            target_snapshot=_target_snapshot(target),
        )


@dataclass(frozen=True, slots=True)
class DurablePublicationAttempt:
    """One persisted provider attempt and its terminal evidence, if any."""

    request: PublicationAttemptRequest
    attempt_number: int
    status: PublicationAttemptStatus
    external_reference: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    receipt: PublicationReceipt | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS publication_attempts (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    episode_version_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_snapshot TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    external_reference TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, project_id, episode_version_id, target_id,
        idempotency_key, attempt_number
    )
);
CREATE TABLE IF NOT EXISTS publication_receipts (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    episode_version_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, project_id, episode_version_id, target_id, idempotency_key)
);
"""


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PublicationRepositoryError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise PublicationRepositoryError("persisted timestamp is invalid")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise PublicationRepositoryError("persisted timestamp must be timezone-aware")
    return timestamp


def _json_text(value: Mapping[str, object]) -> str:
    return json.dumps(_thaw_target(value), sort_keys=True, separators=(",", ":"))


def _thaw_target(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_target(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_target(item) for item in value]
    return value


def _freeze_target(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PublicationRepositoryError("target snapshot must be a mapping")
    normalized = json.loads(_json_text(value))
    if not isinstance(normalized, dict):
        raise PublicationRepositoryError("target snapshot must serialize to an object")
    frozen = _freeze_json(normalized)
    if not isinstance(frozen, Mapping):
        raise PublicationRepositoryError("target snapshot must serialize to an object")
    return cast(Mapping[str, object], frozen)


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _target_snapshot(target: PublicationTarget) -> dict[str, object]:
    return {
        "target_id": target.target_id,
        "kind": target.kind,
        "show_id": target.show_id,
        "feed_url": target.feed_url,
        "visibility": target.visibility,
        "update_policy": target.update_policy,
        "disclosure": {
            "spoken": target.disclosure.spoken,
            "show_notes": target.disclosure.show_notes,
            "platform": target.disclosure.platform,
        },
    }


def _receipt_from_json(value: object) -> PublicationReceipt:
    try:
        if not isinstance(value, str):
            raise TypeError
        payload = json.loads(value)
        authorization = payload["authorization"]
        disclosure = payload["disclosure"]
        if not isinstance(authorization, dict) or not isinstance(disclosure, dict):
            raise TypeError
        return PublicationReceipt(
            publication_id=payload["publication_id"],
            tenant_id=UUID(payload["tenant_id"]),
            project_id=UUID(payload["project_id"]),
            episode_version_id=payload["episode_version_id"],
            target_id=payload["target_id"],
            idempotency_key=payload["idempotency_key"],
            package_sha256=payload["package_sha256"],
            external_id=payload["external_id"],
            status=payload["status"],
            authorization=PublicationAuthorization(**authorization),
            disclosure=DisclosurePolicy(**disclosure),
            provenance=payload["provenance"],
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        PublishingValidationError,
    ) as error:
        raise PublicationRepositoryError(
            "persisted publication receipt is invalid"
        ) from error


def _request_from_row(row: sqlite3.Row) -> PublicationAttemptRequest:
    try:
        target_snapshot = json.loads(row["target_snapshot"])
        if not isinstance(target_snapshot, dict):
            raise TypeError
        return PublicationAttemptRequest(
            tenant_id=UUID(row["tenant_id"]),
            project_id=UUID(row["project_id"]),
            episode_version_id=row["episode_version_id"],
            target_id=row["target_id"],
            idempotency_key=row["idempotency_key"],
            package_sha256=row["package_sha256"],
            target_snapshot=target_snapshot,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PublicationRepositoryError(
            "persisted publication attempt is invalid"
        ) from error


def _same_request(
    stored: PublicationAttemptRequest, candidate: PublicationAttemptRequest
) -> bool:
    return stored == candidate


def _request_for(
    value: PublicationAttemptRequest | PublicationReceipt,
    target: PublicationTarget | None,
) -> PublicationAttemptRequest:
    if isinstance(value, PublicationAttemptRequest):
        if target is not None:
            raise PublicationRepositoryError("attempt request does not accept a target")
        return value
    if target is None:
        raise PublicationRepositoryError(
            "receipt attempts require a publication target"
        )
    return PublicationAttemptRequest.from_receipt(value, target)


def _receipt_matches(
    request: PublicationAttemptRequest, receipt: PublicationReceipt
) -> bool:
    return (
        request.tenant_id == receipt.tenant_id
        and request.project_id == receipt.project_id
        and request.episode_version_id == receipt.episode_version_id
        and request.target_id == receipt.target_id
        and request.idempotency_key == receipt.idempotency_key
        and request.package_sha256 == receipt.package_sha256
    )


def _scope_parameters(
    request: PublicationAttemptRequest,
) -> tuple[str, str, str, str, str]:
    return (
        str(request.tenant_id),
        str(request.project_id),
        request.episode_version_id,
        request.target_id,
        request.idempotency_key,
    )


def _load_pending_attempt(
    connection: sqlite3.Connection, attempt: DurablePublicationAttempt
) -> DurablePublicationAttempt:
    row = connection.execute(
        """SELECT * FROM publication_attempts
           WHERE tenant_id = ? AND project_id = ? AND episode_version_id = ?
             AND target_id = ? AND idempotency_key = ? AND attempt_number = ?""",
        (*_scope_parameters(attempt.request), attempt.attempt_number),
    ).fetchone()
    if row is None:
        raise PublicationAttemptNotFound("publication attempt is not pending")
    stored = _attempt_from_row(row)
    if stored != attempt or stored.status != "pending":
        raise PublicationAttemptNotFound("publication attempt is not pending")
    receipt = connection.execute(
        """SELECT 1 FROM publication_receipts
           WHERE tenant_id = ? AND project_id = ? AND episode_version_id = ?
             AND target_id = ? AND idempotency_key = ?""",
        _scope_parameters(attempt.request),
    ).fetchone()
    if receipt is not None:
        raise PublicationAttemptNotFound("publication attempt is not pending")
    return stored


class SQLitePublicationReceiptRepository:
    """Durable publication evidence adapter over an injected SQLite factory."""

    def __init__(
        self,
        database: Path | str | None = None,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if (database is None) == (connection_factory is None):
            raise ValueError("provide exactly one database path or connection factory")
        if connection_factory is None:
            if database is None:
                raise ValueError("a database path is required")
            path = Path(database)
            path.parent.mkdir(parents=True, exist_ok=True)
            connection_factory = _sqlite_connection_factory(path)
        self._connections = connection_factory
        with self._connections(True) as connection:
            connection.executescript(_SCHEMA)

    def lookup(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_version_id: str,
        target_id: str,
        idempotency_key: str,
    ) -> DurablePublicationAttempt | None:
        """Read the latest scoped attempt without requiring package bytes."""
        with self._connections(False) as connection:
            row = connection.execute(
                """SELECT * FROM publication_attempts
                   WHERE tenant_id = ? AND project_id = ? AND episode_version_id = ?
                     AND target_id = ? AND idempotency_key = ?
                   ORDER BY attempt_number DESC LIMIT 1""",
                (
                    str(tenant_id),
                    str(project_id),
                    episode_version_id,
                    target_id,
                    idempotency_key,
                ),
            ).fetchone()
            if row is None:
                return None
            receipt = None
            if row["status"] == "succeeded":
                receipt_row = connection.execute(
                    """SELECT receipt_json FROM publication_receipts
                       WHERE tenant_id = ? AND project_id = ?
                         AND episode_version_id = ?
                         AND target_id = ? AND idempotency_key = ?""",
                    (
                        str(tenant_id),
                        str(project_id),
                        episode_version_id,
                        target_id,
                        idempotency_key,
                    ),
                ).fetchone()
                if receipt_row is None:
                    raise PublicationRepositoryError(
                        "successful attempt lacks its immutable receipt"
                    )
                receipt = _receipt_from_json(receipt_row["receipt_json"])
                stored = _attempt_from_row(row)
                if (
                    not _receipt_matches(stored.request, receipt)
                    or receipt.external_id != row["external_reference"]
                ):
                    raise PublicationRepositoryError(
                        "successful attempt receipt does not match its identity"
                    )
            return _attempt_from_row(row, receipt)

    def begin(
        self,
        value: PublicationAttemptRequest | PublicationReceipt,
        target: PublicationTarget | datetime | None = None,
        occurred_at: datetime | None = None,
    ) -> DurablePublicationAttempt:
        """Start a safe retry or return the exact prior successful receipt."""
        if isinstance(target, datetime):
            if occurred_at is not None:
                raise PublicationRepositoryError("attempt timestamp is duplicated")
            occurred_at = target
            target = None
        request = _request_for(
            value, target if isinstance(target, PublicationTarget) else None
        )
        timestamp = occurred_at or datetime.now(UTC)
        with self._connections(True) as connection:
            row = connection.execute(
                """SELECT * FROM publication_attempts
                   WHERE tenant_id = ? AND project_id = ? AND episode_version_id = ?
                     AND target_id = ? AND idempotency_key = ?
                   ORDER BY attempt_number DESC LIMIT 1""",
                _scope_parameters(request),
            ).fetchone()
            if row is not None:
                stored = _request_from_row(row)
                if not _same_request(stored, request):
                    raise PublicationIdentityConflict(
                        "idempotency key is bound to another publication"
                    )
                status = row["status"]
                if status == "succeeded":
                    receipt_row = connection.execute(
                        """SELECT receipt_json FROM publication_receipts
                           WHERE tenant_id = ? AND project_id = ?
                             AND episode_version_id = ?
                             AND target_id = ? AND idempotency_key = ?""",
                        _scope_parameters(request),
                    ).fetchone()
                    if receipt_row is None:
                        raise PublicationRepositoryError(
                            "successful attempt lacks its immutable receipt"
                        )
                    receipt = _receipt_from_json(receipt_row[0])
                    if (
                        not _receipt_matches(stored, receipt)
                        or receipt.external_id != row["external_reference"]
                    ):
                        raise PublicationRepositoryError(
                            "successful attempt receipt does not match its identity"
                        )
                    return _attempt_from_row(row, receipt)
                if status in {"pending", "uncertain"}:
                    raise PublicationOutcomeUncertain(
                        "publication outcome is unknown; provider retry is unsafe"
                    )
                attempt_number = int(row["attempt_number"]) + 1
            else:
                attempt_number = 1
            connection.execute(
                """INSERT INTO publication_attempts (
                    tenant_id, project_id, episode_version_id, target_id,
                    target_snapshot, idempotency_key, package_sha256, attempt_number,
                    status, external_reference, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)""",
                (
                    str(request.tenant_id),
                    str(request.project_id),
                    request.episode_version_id,
                    request.target_id,
                    _json_text(request.target_snapshot),
                    request.idempotency_key,
                    request.package_sha256,
                    attempt_number,
                    _timestamp(timestamp),
                    _timestamp(timestamp),
                ),
            )
        return DurablePublicationAttempt(
            request=request,
            attempt_number=attempt_number,
            status="pending",
            external_reference=None,
            error=None,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def succeed(
        self,
        attempt: DurablePublicationAttempt,
        receipt: PublicationReceipt,
        occurred_at: datetime | None = None,
    ) -> DurablePublicationAttempt:
        """Persist one immutable successful receipt for a pending attempt."""
        if not _receipt_matches(attempt.request, receipt):
            raise PublicationIdentityConflict(
                "receipt does not match publication attempt"
            )
        timestamp = occurred_at or datetime.now(UTC)
        with self._connections(True) as connection:
            stored = _load_pending_attempt(connection, attempt)
            result = connection.execute(
                """UPDATE publication_attempts
                   SET status = 'succeeded', external_reference = ?, error = NULL,
                       updated_at = ?
                   WHERE tenant_id = ? AND project_id = ? AND episode_version_id = ?
                     AND target_id = ? AND idempotency_key = ? AND attempt_number = ?
                     AND status = 'pending'""",
                (
                    receipt.external_id,
                    _timestamp(timestamp),
                    *_scope_parameters(stored.request),
                    attempt.attempt_number,
                ),
            )
            if result.rowcount != 1:
                raise PublicationAttemptNotFound("publication attempt is not pending")
            connection.execute(
                """INSERT INTO publication_receipts (
                    tenant_id, project_id, episode_version_id, target_id,
                    idempotency_key, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    *_scope_parameters(stored.request),
                    _json_text(receipt.to_dict()),
                ),
            )
        return DurablePublicationAttempt(
            request=attempt.request,
            attempt_number=attempt.attempt_number,
            status="succeeded",
            external_reference=receipt.external_id,
            error=None,
            created_at=attempt.created_at,
            updated_at=timestamp,
            receipt=receipt,
        )

    def mark_uncertain(
        self,
        attempt: DurablePublicationAttempt,
        error: str,
        occurred_at: datetime | None = None,
        *,
        external_reference: str | None = None,
    ) -> DurablePublicationAttempt:
        """Record an unknown provider outcome and prohibit automatic replay."""
        if not error.strip():
            raise PublicationRepositoryError("uncertain publication error is required")
        safe_error = sanitize_publication_error(error)
        timestamp = occurred_at or datetime.now(UTC)
        with self._connections(True) as connection:
            stored = _load_pending_attempt(connection, attempt)
            result = connection.execute(
                """UPDATE publication_attempts
                   SET status = 'uncertain', external_reference = ?, error = ?,
                       updated_at = ?
                   WHERE tenant_id = ? AND project_id = ? AND episode_version_id = ?
                     AND target_id = ? AND idempotency_key = ? AND attempt_number = ?
                     AND status = 'pending'""",
                (
                    external_reference,
                    safe_error,
                    _timestamp(timestamp),
                    *_scope_parameters(stored.request),
                    attempt.attempt_number,
                ),
            )
        if result.rowcount != 1:
            raise PublicationAttemptNotFound("publication attempt is not pending")
        return DurablePublicationAttempt(
            request=attempt.request,
            attempt_number=attempt.attempt_number,
            status="uncertain",
            external_reference=external_reference,
            error=safe_error,
            created_at=attempt.created_at,
            updated_at=timestamp,
        )

    def fail(
        self,
        attempt: DurablePublicationAttempt,
        error: str,
        occurred_at: datetime | None = None,
    ) -> DurablePublicationAttempt:
        """Record a known failed outcome that may safely be retried."""
        if not error.strip():
            raise PublicationRepositoryError("failed publication error is required")
        safe_error = sanitize_publication_error(error)
        timestamp = occurred_at or datetime.now(UTC)
        with self._connections(True) as connection:
            stored = _load_pending_attempt(connection, attempt)
            result = connection.execute(
                """UPDATE publication_attempts
                   SET status = 'failed', error = ?, updated_at = ?
                   WHERE tenant_id = ? AND project_id = ? AND episode_version_id = ?
                     AND target_id = ? AND idempotency_key = ? AND attempt_number = ?
                     AND status = 'pending'""",
                (
                    safe_error,
                    _timestamp(timestamp),
                    *_scope_parameters(stored.request),
                    attempt.attempt_number,
                ),
            )
            if result.rowcount != 1:
                raise PublicationAttemptNotFound("publication attempt is not pending")
        return DurablePublicationAttempt(
            request=stored.request,
            attempt_number=stored.attempt_number,
            status="failed",
            external_reference=None,
            error=safe_error,
            created_at=stored.created_at,
            updated_at=timestamp,
        )


def _attempt_from_row(
    row: sqlite3.Row, receipt: PublicationReceipt | None = None
) -> DurablePublicationAttempt:
    status = row["status"]
    if status not in {"pending", "succeeded", "failed", "uncertain"}:
        raise PublicationRepositoryError(
            "persisted publication attempt status is invalid"
        )
    return DurablePublicationAttempt(
        request=_request_from_row(row),
        attempt_number=int(row["attempt_number"]),
        status=status,
        external_reference=row["external_reference"],
        error=row["error"],
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
        receipt=receipt,
    )


def _sqlite_connection_factory(path: Path) -> ConnectionFactory:
    @contextmanager
    def connection(write: bool) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(path, isolation_level=None if write else "DEFERRED")
        database.row_factory = sqlite3.Row
        try:
            if write:
                database.execute("BEGIN IMMEDIATE")
            yield database
            if write:
                database.commit()
        except BaseException:
            if write:
                database.rollback()
            raise
        finally:
            database.close()

    return connection


__all__ = [
    "DurablePublicationAttempt",
    "PublicationAttemptNotFound",
    "PublicationAttemptRequest",
    "PublicationIdentityConflict",
    "PublicationOutcomeUncertain",
    "PublicationRepositoryError",
    "SQLitePublicationReceiptRepository",
]
