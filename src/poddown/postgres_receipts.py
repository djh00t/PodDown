"""Tenant-scoped PostgreSQL command receipts for durable workflow dispatch."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from poddown.api.models import CommandReceipt
from poddown.api.runtime import CommandName
from poddown.api.temporal_dispatcher import (
    CommandIdentity,
    CommandReceiptStore,
)
from poddown.episode_service import IdempotencyConflict
from poddown.persistence import PersistenceIntegrityError


class PostgresReceiptCursor(Protocol):
    """Minimal DB-API cursor surface used by the receipt adapter."""

    rowcount: int

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresReceiptCursor:
        """Execute one parameterized statement."""

    def fetchone(self) -> Sequence[object] | None:
        """Return one row from the last query."""


class PostgresReceiptConnection(Protocol):
    """Minimal transaction surface compatible with psycopg and test doubles."""

    def cursor(self) -> PostgresReceiptCursor:
        """Return a cursor for the current transaction."""

    def transaction(self) -> AbstractContextManager[object]:
        """Return a transaction context manager."""


ConnectionFactory = Callable[[], PostgresReceiptConnection]

_RECEIPT_COLUMNS = (
    "project_id, episode_id, command_id, command, idempotency_key, accepted, "
    "state, workflow_id, created_at, updated_at, command_payload"
)
_COMMANDS = frozenset({"create", "render", "publish"})
_STATES = frozenset({"queued", "dispatched", "running", "completed", "failed"})


def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


def _set_tenant(cursor: PostgresReceiptCursor, tenant_id: UUID) -> None:
    cursor.execute(
        "SELECT set_config('app.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def _uuid7(value: object, field: str) -> UUID:
    parsed = value if isinstance(value, UUID) else UUID(str(value))
    if parsed.version != 7:
        raise ValueError(f"{field} must be a UUIDv7")
    return parsed


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} is invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is invalid")
    return value


def _canonical_payload(value: Mapping[str, object]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))


def _stored_payload(value: object) -> str:
    parsed: object = value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("command payload is invalid") from error
    if not isinstance(parsed, Mapping):
        raise ValueError("command payload must be a mapping")
    return _canonical_payload(cast(Mapping[str, object], parsed))


def _receipt_from_row(row: Sequence[object]) -> CommandReceipt:
    """Reconstruct a receipt and reject malformed durable state."""
    if len(row) != 11:
        raise PersistenceIntegrityError(
            "persisted PostgreSQL command receipt has an unexpected shape"
        )
    try:
        (
            _project_id,
            episode_id,
            command_id,
            command,
            idempotency_key,
            accepted,
            state,
            workflow_id,
            created_at,
            _updated_at,
            _payload,
        ) = row
        parsed_command = _text(command, "command")
        parsed_state = _text(state, "state")
        if parsed_command not in _COMMANDS or parsed_state not in _STATES:
            raise ValueError("command receipt enum is invalid")
        if type(accepted) is not bool:
            raise ValueError("accepted is invalid")
        parsed_workflow = (
            _text(workflow_id, "workflow_id") if workflow_id is not None else None
        )
        return CommandReceipt(
            command_id=_uuid7(command_id, "command_id"),
            episode_id=_uuid7(episode_id, "episode_id"),
            idempotency_key=_text(idempotency_key, "idempotency_key"),
            command=cast(CommandName, parsed_command),
            accepted=accepted,
            state=cast(
                Literal["queued", "dispatched", "running", "completed", "failed"],
                parsed_state,
            ),
            workflow_id=parsed_workflow,
            created_at=_timestamp(created_at, "created_at"),
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, PersistenceIntegrityError):
            raise
        raise PersistenceIntegrityError(
            "persisted PostgreSQL command receipt is invalid"
        ) from error


def _same_identity(
    existing: Sequence[object],
    identity: CommandIdentity,
    receipt: CommandReceipt,
) -> bool:
    """Compare the full durable command identity, including project scope."""
    if len(existing) < 4:
        return False
    project_id, episode_id, _command_id, command = existing[:4]
    try:
        return (
            _uuid7(project_id, "project_id") == identity[0]
            and _uuid7(episode_id, "episode_id") == identity[1]
            and command == identity[2]
            and receipt.episode_id == identity[1]
            and receipt.command == identity[2]
        )
    except ValueError:
        return False


class PostgresCommandReceiptStore(CommandReceiptStore):
    """Durable receipt store with tenant RLS and explicit state transitions."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def reserve(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: CommandIdentity,
        receipt: CommandReceipt,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt:
        """Insert or replay one queued command receipt atomically."""
        _uuid7(tenant_id, "tenant_id")
        project_id, episode_id, command = identity
        _uuid7(project_id, "project_id")
        _uuid7(episode_id, "episode_id")
        if command not in _COMMANDS:
            raise ValueError("command is invalid")
        _text(idempotency_key, "idempotency_key")
        if receipt.episode_id != episode_id or receipt.command != command:
            raise IdempotencyConflict()
        requested_payload = _canonical_payload(payload or {})
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                existing_row = self._find_by_key(cursor, tenant_id, idempotency_key)
                if existing_row is not None:
                    existing = _receipt_from_row(existing_row)
                    if not _same_identity(existing_row, identity, existing):
                        raise IdempotencyConflict()
                    if existing.workflow_id != receipt.workflow_id:
                        raise IdempotencyConflict()
                    if existing.accepted != receipt.accepted:
                        raise IdempotencyConflict()
                    if _stored_payload(existing_row[-1]) != requested_payload:
                        raise IdempotencyConflict()
                    return existing
                created_at = receipt.created_at or datetime.now(UTC)
                _timestamp(created_at, "created_at")
                cursor.execute(
                    """INSERT INTO command_receipts (
                    tenant_id, project_id, episode_id, command, idempotency_key,
                    command_id, accepted, state, created_at, updated_at,
                    workflow_id, command_payload
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING""",
                    (
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        command,
                        idempotency_key,
                        str(receipt.command_id),
                        receipt.accepted,
                        receipt.state,
                        created_at,
                        created_at,
                        receipt.workflow_id,
                        requested_payload,
                    ),
                )
                persisted_row = self._find_by_key(cursor, tenant_id, idempotency_key)
                if persisted_row is None:
                    raise IdempotencyConflict()
                persisted = _receipt_from_row(persisted_row)
                if not _same_identity(persisted_row, identity, persisted):
                    raise IdempotencyConflict()
                if _stored_payload(persisted_row[-1]) != requested_payload:
                    raise IdempotencyConflict()
                return persisted
        finally:
            _close(connection)

    def reconcile_dispatched(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: CommandIdentity,
        receipt: CommandReceipt,
    ) -> CommandReceipt:
        """Advance a queued receipt exactly once after workflow acceptance."""
        _uuid7(tenant_id, "tenant_id")
        _text(idempotency_key, "idempotency_key")
        if receipt.state != "dispatched" or receipt.workflow_id is None:
            raise IdempotencyConflict()
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                row = self._find_by_key(cursor, tenant_id, idempotency_key)
                if row is None:
                    raise IdempotencyConflict()
                existing = _receipt_from_row(row)
                if (
                    not _same_identity(row, identity, existing)
                    or existing.command_id != receipt.command_id
                ):
                    raise IdempotencyConflict()
                if existing.state == "dispatched":
                    if existing.workflow_id != receipt.workflow_id:
                        raise IdempotencyConflict()
                    return existing
                if existing.state != "queued":
                    raise IdempotencyConflict()
                cursor.execute(
                    """UPDATE command_receipts SET
                    state = %s, workflow_id = %s, updated_at = %s
                    WHERE tenant_id = %s AND idempotency_key = %s
                    AND state = 'queued'""",
                    (
                        receipt.state,
                        receipt.workflow_id,
                        receipt.created_at or datetime.now(UTC),
                        str(tenant_id),
                        idempotency_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise IdempotencyConflict()
                updated = self._find_by_key(cursor, tenant_id, idempotency_key)
                if updated is None:
                    raise PersistenceIntegrityError(
                        "dispatched command receipt could not be reloaded"
                    )
                return _receipt_from_row(updated)
        finally:
            _close(connection)

    def replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID | None = None,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt | None:
        """Return a matching receipt without changing its state."""
        _uuid7(tenant_id, "tenant_id")
        if project_id is None:
            raise ValueError("project_id is required for PostgreSQL replay")
        _uuid7(project_id, "project_id")
        _uuid7(episode_id, "episode_id")
        if command not in _COMMANDS:
            raise ValueError("command is invalid")
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                cursor.execute(
                    f"SELECT {_RECEIPT_COLUMNS} FROM command_receipts "
                    "WHERE tenant_id = %s AND project_id = %s AND episode_id = %s "
                    "AND command = %s AND idempotency_key = %s",
                    (
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        command,
                        idempotency_key,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                receipt = _receipt_from_row(row)
                if payload is not None and _stored_payload(
                    row[-1]
                ) != _canonical_payload(payload):
                    raise IdempotencyConflict()
                return receipt
        finally:
            _close(connection)

    @staticmethod
    def _find_by_key(
        cursor: PostgresReceiptCursor,
        tenant_id: UUID,
        idempotency_key: str,
    ) -> Sequence[object] | None:
        cursor.execute(
            f"SELECT {_RECEIPT_COLUMNS} FROM command_receipts "
            "WHERE tenant_id = %s AND idempotency_key = %s FOR UPDATE",
            (str(tenant_id), idempotency_key),
        )
        return cursor.fetchone()


__all__ = [
    "ConnectionFactory",
    "PostgresCommandReceiptStore",
    "PostgresReceiptConnection",
    "PostgresReceiptCursor",
]
