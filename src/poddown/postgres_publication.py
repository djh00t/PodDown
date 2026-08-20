"""Tenant-scoped PostgreSQL publication receipt persistence."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID

from poddown.persistence import PersistenceIntegrityError
from poddown.publishing import (
    DisclosurePolicy,
    PublicationAuthorization,
    PublicationConflictError,
    PublicationReceipt,
    PublishingError,
)


class PostgresPublicationCursor(Protocol):
    """Minimal DB-API cursor surface used by the receipt repository."""

    rowcount: int

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresPublicationCursor:
        """Execute one parameterized statement."""

    def fetchone(self) -> Sequence[object] | None:
        """Return one row from the last query."""


class PostgresPublicationConnection(Protocol):
    """Minimal transaction surface compatible with psycopg and test doubles."""

    def cursor(self) -> PostgresPublicationCursor:
        """Return a cursor for the current transaction."""

    def transaction(self) -> AbstractContextManager[object]:
        """Return a transaction context manager."""


ConnectionFactory = Callable[[], PostgresPublicationConnection]

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TEXT = re.compile(r"^\S[\s\S]{0,254}$")
_COLUMNS = (
    "project_id, publication_id, episode_version_id, target_id, "
    'idempotency_key, package_sha256, external_id, status, "authorization", '
    "disclosure, provenance, created_at"
)


def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


def _set_tenant(cursor: PostgresPublicationCursor, tenant_id: UUID) -> None:
    cursor.execute(
        "SELECT set_config('app.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def _uuid7(value: object, field: str) -> UUID:
    parsed = value if isinstance(value, UUID) else UUID(str(value))
    if parsed.version != 7:
        raise ValueError(f"{field} must be a UUIDv7")
    return parsed


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or _TEXT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} is invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _mapping(value: object, field: str) -> Mapping[str, object]:
    parsed: object = value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"{field} is invalid") from error
    if not isinstance(parsed, Mapping) or any(
        not isinstance(key, str) for key in parsed
    ):
        raise ValueError(f"{field} is invalid")
    return cast(Mapping[str, object], parsed)


def _authorization(value: object) -> PublicationAuthorization:
    data = _mapping(value, "authorization")
    try:
        return PublicationAuthorization(
            actor_id=_text(data["actor_id"], "authorization.actor_id"),
            decision_id=_text(data["decision_id"], "authorization.decision_id"),
            reason=_text(data["reason"], "authorization.reason"),
            operation=_text(data["operation"], "authorization.operation"),
        )
    except (KeyError, TypeError, ValueError, PublishingError) as error:
        raise ValueError("authorization is invalid") from error


def _boolean(data: Mapping[str, object], field: str) -> bool:
    value = data.get(field)
    if type(value) is not bool:
        raise ValueError(f"{field} must be boolean")
    return value


def _disclosure(value: object) -> DisclosurePolicy:
    data = _mapping(value, "disclosure")
    try:
        return DisclosurePolicy(
            spoken=_boolean(data, "spoken"),
            show_notes=_boolean(data, "show_notes"),
            platform=_boolean(data, "platform"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("disclosure is invalid") from error


def _receipt_from_row(tenant_id: UUID, row: Sequence[object]) -> PublicationReceipt:
    """Reconstruct a receipt and reject malformed durable state."""
    if len(row) != 12:
        raise PersistenceIntegrityError(
            "persisted PostgreSQL publication receipt has an unexpected shape"
        )
    try:
        (
            project_id,
            publication_id,
            episode_version_id,
            target_id,
            idempotency_key,
            package_sha256,
            external_id,
            status,
            authorization,
            disclosure,
            provenance,
            created_at,
        ) = row
        _timestamp(created_at, "created_at")
        return PublicationReceipt(
            publication_id=str(_uuid7(publication_id, "publication_id")),
            tenant_id=tenant_id,
            project_id=_uuid7(project_id, "project_id"),
            episode_version_id=_text(episode_version_id, "episode_version_id"),
            target_id=_text(target_id, "target_id"),
            idempotency_key=_text(idempotency_key, "idempotency_key"),
            package_sha256=_digest(package_sha256, "package_sha256"),
            external_id=_text(external_id, "external_id"),
            status=_text(status, "status"),
            authorization=_authorization(authorization),
            disclosure=_disclosure(disclosure),
            provenance=dict(_mapping(provenance, "provenance")),
        )
    except (KeyError, TypeError, ValueError, PublishingError) as error:
        if isinstance(error, PersistenceIntegrityError):
            raise
        raise PersistenceIntegrityError(
            "persisted PostgreSQL publication receipt is invalid"
        ) from error


def _json_text(value: Mapping[str, object]) -> str:
    return json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
    )


def _disclosure_dict(policy: DisclosurePolicy) -> dict[str, bool]:
    return {
        "spoken": policy.spoken,
        "show_notes": policy.show_notes,
        "platform": policy.platform,
    }


def _same_receipt(left: PublicationReceipt, right: PublicationReceipt) -> bool:
    return left.to_dict() == right.to_dict()


class PostgresPublicationReceiptRepository:
    """Persist immutable publication outcomes under PostgreSQL tenant RLS."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def save(self, receipt: PublicationReceipt) -> PublicationReceipt:
        """Insert or replay one receipt, rejecting immutable conflicts."""
        if not isinstance(receipt, PublicationReceipt):
            raise TypeError("receipt must be a PublicationReceipt")
        _uuid7(receipt.tenant_id, "tenant_id")
        _uuid7(receipt.project_id, "project_id")
        _uuid7(receipt.publication_id, "publication_id")
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, receipt.tenant_id)
                existing_row = self._find_by_key(
                    cursor,
                    receipt.tenant_id,
                    receipt.target_id,
                    receipt.idempotency_key,
                )
                if existing_row is not None:
                    existing = _receipt_from_row(receipt.tenant_id, existing_row)
                    if not _same_receipt(existing, receipt):
                        raise PublicationConflictError(
                            "publication idempotency key is bound to another receipt"
                        )
                    return existing
                cursor.execute(
                    """INSERT INTO publication_receipts (
                    tenant_id, project_id, publication_id, episode_version_id,
                    target_id, idempotency_key, package_sha256, status,
                    external_id, "authorization", disclosure, provenance, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) ON CONFLICT (tenant_id, target_id, idempotency_key) DO NOTHING""",
                    (
                        str(receipt.tenant_id),
                        str(receipt.project_id),
                        str(UUID(receipt.publication_id)),
                        receipt.episode_version_id,
                        receipt.target_id,
                        receipt.idempotency_key,
                        receipt.package_sha256,
                        receipt.status,
                        receipt.external_id,
                        _json_text(receipt.authorization.to_dict()),
                        _json_text(_disclosure_dict(receipt.disclosure)),
                        _json_text(
                            cast(
                                Mapping[str, object],
                                receipt.to_dict()["provenance"],
                            )
                        ),
                        datetime.now(UTC),
                    ),
                )
                persisted_row = self._find_by_key(
                    cursor,
                    receipt.tenant_id,
                    receipt.target_id,
                    receipt.idempotency_key,
                )
                if persisted_row is None:
                    raise PersistenceIntegrityError(
                        "publication receipt insert was not observable"
                    )
                persisted = _receipt_from_row(receipt.tenant_id, persisted_row)
                if not _same_receipt(persisted, receipt):
                    raise PublicationConflictError(
                        "publication idempotency key is bound to another receipt"
                    )
                return persisted
        finally:
            _close(connection)

    def get_by_idempotency(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        target_id: str,
        idempotency_key: str,
    ) -> PublicationReceipt | None:
        """Read a receipt only when it belongs to the requested project."""
        _uuid7(tenant_id, "tenant_id")
        _uuid7(project_id, "project_id")
        _text(target_id, "target_id")
        _text(idempotency_key, "idempotency_key")
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                row = self._find_by_key(cursor, tenant_id, target_id, idempotency_key)
                if row is None:
                    return None
                receipt = _receipt_from_row(tenant_id, row)
                return receipt if receipt.project_id == project_id else None
        finally:
            _close(connection)

    @staticmethod
    def _find_by_key(
        cursor: PostgresPublicationCursor,
        tenant_id: UUID,
        target_id: str,
        idempotency_key: str,
    ) -> Sequence[object] | None:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM publication_receipts "
            "WHERE tenant_id = %s AND target_id = %s AND idempotency_key = %s "
            "FOR UPDATE",
            (str(tenant_id), target_id, idempotency_key),
        )
        return cursor.fetchone()


__all__ = [
    "ConnectionFactory",
    "PostgresPublicationConnection",
    "PostgresPublicationCursor",
    "PostgresPublicationReceiptRepository",
]
