"""Tenant-scoped PostgreSQL transactional-outbox contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol
from uuid import UUID


class OutboxError(ValueError):
    """Base error for immutable outbox contract failures."""


class OutboxConflict(OutboxError):
    """An event identity was reused with different immutable data."""


class OutboxNotFound(OutboxError):
    """The requested tenant-scoped event does not exist."""


class OutboxIntegrityError(OutboxError, RuntimeError):
    """Persisted outbox state cannot be reconstructed safely."""


class OutboxCursor(Protocol):
    """Minimal DB-API cursor surface used by the adapter."""

    rowcount: int

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> OutboxCursor:
        """Execute one parameterized PostgreSQL statement."""

    def fetchone(self) -> Sequence[object] | None:
        """Fetch one row, if present."""

    def fetchall(self) -> list[Sequence[object]]:
        """Fetch all rows from the latest query."""


class OutboxConnection(Protocol):
    """Connection port; callers own the surrounding transaction."""

    def cursor(self) -> OutboxCursor:
        """Return a cursor in the caller's current transaction."""


def _uuid7(value: UUID, field: str) -> UUID:
    if not isinstance(value, UUID) or value.version != 7:
        raise OutboxError(f"{field} must be a UUIDv7")
    return value


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutboxError(f"{field} must be non-empty")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OutboxError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise OutboxError("outbox payload contains an unsupported value")


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Immutable event written in the same transaction as authoritative state."""

    tenant_id: UUID
    project_id: UUID
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: Mapping[str, object]
    created_at: datetime
    published_at: datetime | None = None
    attempt_count: int = 0

    def __post_init__(self) -> None:
        _uuid7(self.tenant_id, "tenant_id")
        _uuid7(self.project_id, "project_id")
        _uuid7(self.event_id, "event_id")
        _uuid7(self.aggregate_id, "aggregate_id")
        _text(self.aggregate_type, "aggregate_type")
        _text(self.event_type, "event_type")
        if not isinstance(self.payload, Mapping):
            raise OutboxError("payload must be a mapping")
        frozen = _freeze(self.payload)
        if not isinstance(frozen, Mapping):
            raise OutboxError("payload must be a mapping")
        object.__setattr__(self, "payload", frozen)
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.published_at is not None:
            object.__setattr__(
                self, "published_at", _utc(self.published_at, "published_at")
            )
        if type(self.attempt_count) is not int or self.attempt_count < 0:
            raise OutboxError("attempt_count must be a non-negative integer")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-shaped representation without database metadata."""
        return {
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "event_id": str(self.event_id),
            "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id),
            "event_type": self.event_type,
            "payload": _thaw(self.payload),
            "created_at": self.created_at.isoformat(),
            "published_at": (
                self.published_at.isoformat() if self.published_at is not None else None
            ),
            "attempt_count": self.attempt_count,
        }


def _set_tenant(cursor: OutboxCursor, tenant_id: UUID) -> None:
    cursor.execute(
        "SELECT set_config('poddown.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def _payload_from_database(value: object) -> Mapping[str, object]:
    parsed: object
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise OutboxIntegrityError("outbox payload is invalid JSON") from error
    else:
        parsed = value
    if not isinstance(parsed, Mapping):
        raise OutboxIntegrityError("outbox payload is not an object")
    return parsed


def _row_event(tenant_id: UUID, row: Sequence[object]) -> OutboxEvent:
    if len(row) != 9:
        raise OutboxIntegrityError("outbox row has an unexpected shape")
    try:
        (
            event_id,
            project_id,
            aggregate_type,
            aggregate_id,
            event_type,
            payload,
            created_at,
            published_at,
            attempts,
        ) = row
        if not isinstance(event_id, UUID):
            event_id = UUID(str(event_id))
        if not isinstance(project_id, UUID):
            project_id = UUID(str(project_id))
        if not isinstance(aggregate_id, UUID):
            aggregate_id = UUID(str(aggregate_id))
        if not isinstance(aggregate_type, str):
            raise TypeError("aggregate_type")
        if not isinstance(event_type, str):
            raise TypeError("event_type")
        if not isinstance(created_at, datetime):
            raise TypeError("created_at")
        if published_at is not None and not isinstance(published_at, datetime):
            raise TypeError("published_at")
        if type(attempts) is not int:
            raise TypeError("attempt_count")
        return OutboxEvent(
            tenant_id=tenant_id,
            project_id=project_id,
            event_id=event_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=_payload_from_database(payload),
            created_at=created_at,
            published_at=published_at,
            attempt_count=attempts,
        )
    except (OutboxError, TypeError, ValueError) as error:
        if isinstance(error, OutboxIntegrityError):
            raise
        raise OutboxIntegrityError("outbox row is invalid") from error


_SELECT_COLUMNS = (
    "event_id, project_id, aggregate_type, aggregate_id, event_type, "
    "payload, created_at, published_at, attempt_count"
)


class PostgresOutbox:
    """Replay-safe PostgreSQL outbox operations on caller-owned transactions."""

    def append(self, connection: OutboxConnection, event: OutboxEvent) -> OutboxEvent:
        """Insert or replay an event without committing the surrounding transaction."""
        if not isinstance(event, OutboxEvent):
            raise OutboxError("event must be an OutboxEvent")
        cursor = connection.cursor()
        _set_tenant(cursor, event.tenant_id)
        existing = self._get(cursor, event.tenant_id, event.event_id)
        if existing is not None:
            return self._same_or_conflict(existing, event)
        cursor.execute(
            """INSERT INTO outbox_events (
                tenant_id, project_id, event_id, aggregate_type, aggregate_id,
                event_type, payload, created_at, published_at, attempt_count
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
            ON CONFLICT (tenant_id, event_id) DO NOTHING""",
            (
                str(event.tenant_id),
                str(event.project_id),
                str(event.event_id),
                event.aggregate_type,
                str(event.aggregate_id),
                event.event_type,
                json.dumps(_thaw(event.payload), sort_keys=True, separators=(",", ":")),
                event.created_at,
                event.attempt_count,
            ),
        )
        inserted = self._get(cursor, event.tenant_id, event.event_id)
        if inserted is None:
            raise OutboxIntegrityError("outbox insert was not observable")
        return self._same_or_conflict(inserted, event)

    def pending(
        self,
        connection: OutboxConnection,
        tenant_id: UUID,
        *,
        limit: int = 100,
    ) -> tuple[OutboxEvent, ...]:
        """Lock a bounded batch of unpublished events in the caller's transaction."""
        _uuid7(tenant_id, "tenant_id")
        if type(limit) is not int or not 0 < limit <= 1000:
            raise OutboxError("limit must be between 1 and 1000")
        cursor = connection.cursor()
        _set_tenant(cursor, tenant_id)
        cursor.execute(
            f"""SELECT {_SELECT_COLUMNS} FROM outbox_events
                WHERE tenant_id = %s AND published_at IS NULL
                ORDER BY created_at, event_id
                LIMIT %s
                FOR UPDATE SKIP LOCKED""",
            (str(tenant_id), limit),
        )
        return tuple(_row_event(tenant_id, row) for row in cursor.fetchall())

    def mark_published(
        self,
        connection: OutboxConnection,
        tenant_id: UUID,
        event_id: UUID,
        published_at: datetime,
    ) -> OutboxEvent:
        """Acknowledge one event once; later acknowledgements cannot rewrite time."""
        _uuid7(tenant_id, "tenant_id")
        _uuid7(event_id, "event_id")
        timestamp = _utc(published_at, "published_at")
        cursor = connection.cursor()
        _set_tenant(cursor, tenant_id)
        existing = self._get(cursor, tenant_id, event_id)
        if existing is None:
            raise OutboxNotFound("outbox event was not found")
        if existing.published_at is not None:
            return existing
        cursor.execute(
            """UPDATE outbox_events SET published_at = %s
               WHERE tenant_id = %s AND event_id = %s AND published_at IS NULL""",
            (timestamp, str(tenant_id), str(event_id)),
        )
        updated = self._get(cursor, tenant_id, event_id)
        if updated is None:
            raise OutboxIntegrityError("published outbox event disappeared")
        return updated

    def record_attempt(
        self,
        connection: OutboxConnection,
        tenant_id: UUID,
        event_id: UUID,
    ) -> OutboxEvent:
        """Increment retry evidence without changing immutable event identity."""
        _uuid7(tenant_id, "tenant_id")
        _uuid7(event_id, "event_id")
        cursor = connection.cursor()
        _set_tenant(cursor, tenant_id)
        existing = self._get(cursor, tenant_id, event_id)
        if existing is None:
            raise OutboxNotFound("outbox event was not found")
        if existing.published_at is not None:
            return existing
        cursor.execute(
            """UPDATE outbox_events SET attempt_count = attempt_count + 1
               WHERE tenant_id = %s AND event_id = %s AND published_at IS NULL""",
            (str(tenant_id), str(event_id)),
        )
        updated = self._get(cursor, tenant_id, event_id)
        if updated is None:
            raise OutboxIntegrityError("outbox event disappeared after retry update")
        return updated

    @staticmethod
    def _get(
        cursor: OutboxCursor, tenant_id: UUID, event_id: UUID
    ) -> OutboxEvent | None:
        cursor.execute(
            f"SELECT {_SELECT_COLUMNS} FROM outbox_events "
            "WHERE tenant_id = %s AND event_id = %s",
            (str(tenant_id), str(event_id)),
        )
        row = cursor.fetchone()
        return _row_event(tenant_id, row) if row is not None else None

    @staticmethod
    def _same_or_conflict(existing: OutboxEvent, requested: OutboxEvent) -> OutboxEvent:
        immutable_existing = (
            existing.tenant_id,
            existing.project_id,
            existing.event_id,
            existing.aggregate_type,
            existing.aggregate_id,
            existing.event_type,
            existing.payload,
            existing.created_at,
        )
        immutable_requested = (
            requested.tenant_id,
            requested.project_id,
            requested.event_id,
            requested.aggregate_type,
            requested.aggregate_id,
            requested.event_type,
            requested.payload,
            requested.created_at,
        )
        if immutable_existing != immutable_requested:
            raise OutboxConflict("outbox event identity conflicts with persisted data")
        return existing


__all__ = [
    "OutboxConnection",
    "OutboxConflict",
    "OutboxCursor",
    "OutboxError",
    "OutboxEvent",
    "OutboxIntegrityError",
    "OutboxNotFound",
    "PostgresOutbox",
]
