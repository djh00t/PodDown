"""Durable DB-API repository seam for scoped one-time publication approvals."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from poddown.tenant_context import tenant_transaction

ApprovalOperation = Literal["publish", "update", "delete"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ApprovalCursor(Protocol):
    rowcount: int

    def execute(self, operation: str, parameters: Sequence[object] = ()) -> object: ...
    def fetchone(self) -> Sequence[object] | None: ...


class ApprovalConnection(Protocol):
    def cursor(self) -> ApprovalCursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class PublicationApprovalError(ValueError):
    """Base error for publication approval persistence failures."""


class ApprovalConflict(PublicationApprovalError):
    """An approval ID or reference was rebound to different evidence."""


class ApprovalUnavailable(PublicationApprovalError):
    """An approval is absent, expired, consumed, or outside caller scope."""


class ApprovalPersistenceError(PublicationApprovalError):
    """Approval persistence failed without a safe replay result."""


@dataclass(frozen=True, slots=True)
class PublicationApproval:
    """Durable scoped authorization evidence for one publication operation."""

    approval_id: UUID
    tenant_id: UUID
    project_id: UUID
    episode_id: UUID
    publication_id: UUID
    operation: ApprovalOperation
    approval_reference: str
    actor_id: str
    nonce_sha256: str
    approved_at: datetime
    expires_at: datetime
    consumed_at: datetime | None

    def immutable_identity(self) -> tuple[object, ...]:
        return (
            self.approval_id,
            self.tenant_id,
            self.project_id,
            self.episode_id,
            self.publication_id,
            self.operation,
            self.approval_reference,
            self.actor_id,
            self.nonce_sha256,
            self.approved_at,
            self.expires_at,
        )


def _timestamp(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _validate_evidence(
    approval_id: UUID,
    tenant_id: UUID,
    project_id: UUID,
    episode_id: UUID,
    publication_id: UUID,
    operation: ApprovalOperation,
    actor_id: str,
    nonce_sha256: str,
) -> None:
    if (
        any(
            not isinstance(value, UUID) or value.version != 7
            for value in (
                approval_id,
                tenant_id,
                project_id,
                episode_id,
                publication_id,
            )
        )
        or operation not in {"publish", "update", "delete"}
        or not isinstance(actor_id, str)
        or not actor_id.strip()
        or not isinstance(nonce_sha256, str)
        or _SHA256.fullmatch(nonce_sha256) is None
    ):
        raise ValueError("publication approval identity is invalid")


def _approval_from_row(row: Sequence[object]) -> PublicationApproval:
    try:
        operation = str(row[5])
        approval = PublicationApproval(
            UUID(str(row[0])),
            UUID(str(row[1])),
            UUID(str(row[2])),
            UUID(str(row[3])),
            UUID(str(row[4])),
            cast(ApprovalOperation, operation),
            str(row[6]),
            str(row[7]),
            str(row[8]),
            datetime.fromisoformat(str(row[9])),
            datetime.fromisoformat(str(row[10])),
            datetime.fromisoformat(str(row[11])) if row[11] else None,
        )
        _validate_evidence(
            approval.approval_id,
            approval.tenant_id,
            approval.project_id,
            approval.episode_id,
            approval.publication_id,
            approval.operation,
            approval.actor_id,
            approval.nonce_sha256,
        )
        _timestamp(approval.approved_at)
        _timestamp(approval.expires_at)
        if approval.consumed_at is not None:
            _timestamp(approval.consumed_at)
        return approval
    except (IndexError, TypeError, ValueError) as error:
        raise PublicationApprovalError("persisted approval is invalid") from error


_SELECT = (
    "SELECT approval.id, approval.tenant_id, publication.project_id, "
    "publication.episode_id, approval.publication_id, approval.operation, "
    "approval.approval_reference, approval.actor_id, approval.nonce_sha256, "
    "approval.approved_at, approval.expires_at, approval.consumed_at "
    "FROM publication_approvals approval JOIN publications publication "
    "ON publication.id = approval.publication_id "
    "AND publication.tenant_id = approval.tenant_id "
    "WHERE approval.tenant_id = ? AND approval.id = ?"
)


def _placeholder(connection: ApprovalConnection) -> str:
    return "?" if isinstance(connection, sqlite3.Connection) else "%s"


def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


class PublicationApprovalRepository:
    """Persist and atomically consume publication approvals through DB-API."""

    def __init__(self, connection_factory: Callable[[], ApprovalConnection]) -> None:
        self._connection_factory = connection_factory

    def record(
        self,
        *,
        approval_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        publication_id: UUID,
        operation: ApprovalOperation,
        approval_reference: str,
        actor_id: str,
        nonce_sha256: str,
        approved_at: datetime,
        expires_at: datetime,
    ) -> PublicationApproval:
        _validate_evidence(
            approval_id,
            tenant_id,
            project_id,
            episode_id,
            publication_id,
            operation,
            actor_id,
            nonce_sha256,
        )
        if not isinstance(approval_reference, str) or not approval_reference.strip():
            raise ValueError("publication approval identity is invalid")
        approved_at_text, expires_at_text = (
            _timestamp(approved_at),
            _timestamp(expires_at),
        )
        if expires_at <= approved_at:
            raise ValueError("approval expiry must follow approval time")
        expected = PublicationApproval(
            approval_id,
            tenant_id,
            project_id,
            episode_id,
            publication_id,
            operation,
            approval_reference,
            actor_id,
            nonce_sha256,
            approved_at,
            expires_at,
            None,
        )
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise ApprovalPersistenceError(
                "publication approval persistence failed"
            ) from error
        placeholder = _placeholder(connection)
        try:
            with tenant_transaction(connection, tenant_id) as cursor:
                cursor.execute(
                    _SELECT.replace("?", placeholder),
                    (str(tenant_id), str(approval_id)),
                )
                row = cursor.fetchone()
                if row is not None:
                    stored = _approval_from_row(row)
                    if stored.immutable_identity() != expected.immutable_identity():
                        raise ApprovalConflict(
                            "publication approval conflicts with evidence"
                        )
                    return stored
                cursor.execute(
                    (
                        "INSERT INTO publication_approvals (id, tenant_id, "
                        "publication_id, operation, approval_reference, actor_id, "
                        "nonce_sha256, approved_at, expires_at, consumed_at, "
                        "created_at) "
                        "SELECT ?, ?, id, ?, ?, ?, ?, ?, ?, "
                        "NULL, ? FROM publications WHERE id = ? AND tenant_id = ? AND "
                        "project_id = ? AND episode_id = ?"
                    ).replace("?", placeholder),
                    (
                        str(approval_id),
                        str(tenant_id),
                        operation,
                        approval_reference,
                        actor_id,
                        nonce_sha256,
                        approved_at_text,
                        expires_at_text,
                        approved_at_text,
                        str(publication_id),
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ApprovalUnavailable("publication approval is unavailable")
                return expected
        except sqlite3.IntegrityError as error:
            try:
                with tenant_transaction(connection, tenant_id) as cursor:
                    cursor.execute(
                        _SELECT.replace("?", placeholder),
                        (str(tenant_id), str(approval_id)),
                    )
                    row = cursor.fetchone()
            except Exception as reread_error:
                raise ApprovalPersistenceError(
                    "publication approval persistence failed"
                ) from reread_error
            if row is None:
                raise ApprovalConflict(
                    "publication approval conflicts with evidence"
                ) from error
            stored = _approval_from_row(row)
            if stored.immutable_identity() != expected.immutable_identity():
                raise ApprovalConflict(
                    "publication approval conflicts with evidence"
                ) from error
            return stored
        except sqlite3.DatabaseError as error:
            raise ApprovalPersistenceError(
                "publication approval persistence failed"
            ) from error
        except PublicationApprovalError:
            raise
        except Exception as error:
            raise ApprovalPersistenceError(
                "publication approval persistence failed"
            ) from error
        finally:
            _close(connection)

    def consume(
        self,
        *,
        approval_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        publication_id: UUID,
        operation: ApprovalOperation,
        actor_id: str,
        nonce_sha256: str,
        consumed_at: datetime,
    ) -> PublicationApproval:
        _validate_evidence(
            approval_id,
            tenant_id,
            project_id,
            episode_id,
            publication_id,
            operation,
            actor_id,
            nonce_sha256,
        )
        consumed_at_text = _timestamp(consumed_at)
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise ApprovalPersistenceError(
                "publication approval persistence failed"
            ) from error
        placeholder = _placeholder(connection)
        try:
            with tenant_transaction(connection, tenant_id) as cursor:
                cursor.execute(
                    (
                        "UPDATE publication_approvals SET consumed_at = ? WHERE id = ? "
                        "AND tenant_id = ? AND publication_id = ? AND operation = ? "
                        "AND actor_id = ? AND nonce_sha256 = ? AND consumed_at IS NULL "
                        "AND approved_at <= ? AND expires_at > ? AND EXISTS (SELECT 1 "
                        "FROM publications WHERE publications.id = "
                        "publication_approvals.publication_id AND "
                        "publications.tenant_id = publication_approvals.tenant_id "
                        "AND publications.project_id = ? "
                        "AND publications.episode_id = ?)"
                    ).replace("?", placeholder),
                    (
                        consumed_at_text,
                        str(approval_id),
                        str(tenant_id),
                        str(publication_id),
                        operation,
                        actor_id,
                        nonce_sha256,
                        consumed_at_text,
                        consumed_at_text,
                        str(project_id),
                        str(episode_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ApprovalUnavailable("publication approval is unavailable")
                cursor.execute(
                    _SELECT.replace("?", placeholder),
                    (str(tenant_id), str(approval_id)),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ApprovalUnavailable("publication approval is unavailable")
                return _approval_from_row(row)
        except sqlite3.DatabaseError as error:
            raise ApprovalPersistenceError(
                "publication approval persistence failed"
            ) from error
        except PublicationApprovalError:
            raise
        except Exception as error:
            raise ApprovalPersistenceError(
                "publication approval persistence failed"
            ) from error
        finally:
            _close(connection)
