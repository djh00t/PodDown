"""Tenant-scoped PostgreSQL transactional-outbox contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import MappingProxyType
from typing import Protocol
from uuid import UUID, uuid4


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
        "SELECT set_config('app.tenant_id', %s, true)",
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


_WRITER_MAX_LEASE = timedelta(minutes=15)
_WRITER_MAX_RETRY_DELAY = timedelta(hours=1)
_WRITER_DEFAULT_LEASE = timedelta(minutes=5)
_WRITER_COLUMNS = (
    "id, tenant_id, aggregate_type, aggregate_id, aggregate_version, event_type, "
    "payload, publish_attempts, published_at, created_at, available_at, "
    "claim_token, lease_until"
)


@dataclass(frozen=True, slots=True)
class _WriterOutboxEvent:
    """SQLite/qmark outbox evidence used by the preserved writer contract."""

    event_id: UUID
    tenant_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    event_type: str
    payload: Mapping[str, object]
    created_at: datetime
    available_at: datetime
    publish_attempts: int = 0
    published_at: datetime | None = None
    claim_token: UUID | None = None
    lease_until: datetime | None = None

    def immutable_identity(self) -> tuple[object, ...]:
        """Return the fields a duplicate enqueue must replay exactly."""
        return (
            self.event_id,
            self.tenant_id,
            self.aggregate_type,
            self.aggregate_id,
            self.aggregate_version,
            self.event_type,
            _writer_payload(self.payload),
            self.created_at,
            self.available_at,
        )


class OutboxSqlAdapter(Protocol):
    """SQLite/qmark SQL port for the preserved transactional writer."""

    def insert_event(self, cursor: OutboxCursor, event: _WriterOutboxEvent) -> None: ...

    def find_event(
        self,
        cursor: OutboxCursor,
        *,
        tenant_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_version: int,
    ) -> Sequence[object] | None: ...

    def find_available(
        self, cursor: OutboxCursor, *, limit: int, now: datetime
    ) -> Sequence[Sequence[object]]: ...

    def reserve(
        self,
        cursor: OutboxCursor,
        *,
        event_id: UUID,
        claim_token: UUID,
        lease_until: datetime,
        now: datetime,
    ) -> bool: ...

    def release_for_retry(
        self,
        cursor: OutboxCursor,
        *,
        event_id: UUID,
        claim_token: UUID,
        available_at: datetime,
        now: datetime,
    ) -> bool: ...


def _writer_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OutboxError("outbox timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _writer_payload(payload: Mapping[str, object]) -> str:
    if not isinstance(payload, Mapping):
        raise OutboxError("outbox payload must be a JSON object")
    try:
        return json.dumps(
            payload, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError) as error:
        raise OutboxError("outbox payload must be JSON serializable") from error


def _writer_event_id(
    tenant_id: UUID, aggregate_type: str, aggregate_id: UUID, aggregate_version: int
) -> UUID:
    source = "\x1f".join(
        (str(tenant_id), aggregate_type, str(aggregate_id), str(aggregate_version))
    )
    value = int.from_bytes(sha256(source.encode("utf-8")).digest()[:16], "big")
    value = (value & ~(0xF << 76)) | (0x7 << 76)
    value = (value & ~(0x3 << 62)) | (0x2 << 62)
    return UUID(int=value)


def _writer_validate(event: _WriterOutboxEvent) -> None:
    if (
        event.event_id.version != 7
        or event.tenant_id.version != 7
        or event.aggregate_id.version != 7
        or not event.aggregate_type.strip()
        or not event.event_type.strip()
        or event.aggregate_version <= 0
        or event.event_id
        != _writer_event_id(
            event.tenant_id,
            event.aggregate_type,
            event.aggregate_id,
            event.aggregate_version,
        )
    ):
        raise OutboxError("outbox event identity is invalid")
    _writer_payload(event.payload)
    _writer_timestamp(event.created_at)
    _writer_timestamp(event.available_at)


def _writer_row_event(row: Sequence[object]) -> _WriterOutboxEvent:
    try:
        payload_value = row[6]
        payload = (
            payload_value
            if isinstance(payload_value, Mapping)
            else json.loads(str(payload_value))
        )
        if not isinstance(payload, Mapping):
            raise ValueError("payload is not an object")
        event = _WriterOutboxEvent(
            event_id=UUID(str(row[0])),
            tenant_id=UUID(str(row[1])),
            aggregate_type=str(row[2]),
            aggregate_id=UUID(str(row[3])),
            aggregate_version=int(str(row[4])),
            event_type=str(row[5]),
            payload=payload,
            publish_attempts=int(str(row[7])),
            published_at=(
                datetime.fromisoformat(str(row[8])) if row[8] is not None else None
            ),
            created_at=datetime.fromisoformat(str(row[9])),
            available_at=datetime.fromisoformat(str(row[10])),
            claim_token=UUID(str(row[11])) if row[11] is not None else None,
            lease_until=(
                datetime.fromisoformat(str(row[12])) if row[12] is not None else None
            ),
        )
        _writer_validate(event)
        return event
    except (IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise OutboxError("persisted outbox event is invalid") from error


class QmarkOutboxSqlAdapter:
    """SQLite/qmark statements for the preserved writer contract."""

    def insert_event(self, cursor: OutboxCursor, event: _WriterOutboxEvent) -> None:
        cursor.execute(
            "INSERT INTO outbox_events (id, tenant_id, aggregate_type, aggregate_id, "
            "aggregate_version, event_type, payload, publish_attempts, published_at, "
            "created_at, available_at, claim_token, lease_until) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT "
            "(tenant_id, aggregate_type, aggregate_id, aggregate_version) DO NOTHING",
            (
                str(event.event_id),
                str(event.tenant_id),
                event.aggregate_type,
                str(event.aggregate_id),
                event.aggregate_version,
                event.event_type,
                _writer_payload(event.payload),
                event.publish_attempts,
                None,
                _writer_timestamp(event.created_at),
                _writer_timestamp(event.available_at),
                None,
                None,
            ),
        )

    def find_event(
        self,
        cursor: OutboxCursor,
        *,
        tenant_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_version: int,
    ) -> Sequence[object] | None:
        cursor.execute(
            f"SELECT {_WRITER_COLUMNS} FROM outbox_events WHERE tenant_id = ? "
            "AND aggregate_type = ? AND aggregate_id = ? AND aggregate_version = ?",
            (str(tenant_id), aggregate_type, str(aggregate_id), aggregate_version),
        )
        return cursor.fetchone()

    def find_available(
        self, cursor: OutboxCursor, *, limit: int, now: datetime
    ) -> Sequence[Sequence[object]]:
        cursor.execute(
            f"SELECT {_WRITER_COLUMNS} FROM outbox_events WHERE published_at IS NULL "
            "AND available_at <= ? AND (claim_token IS NULL OR lease_until <= ?) "
            "ORDER BY available_at, id LIMIT ?",
            (_writer_timestamp(now), _writer_timestamp(now), limit),
        )
        return cursor.fetchall()

    def reserve(
        self,
        cursor: OutboxCursor,
        *,
        event_id: UUID,
        claim_token: UUID,
        lease_until: datetime,
        now: datetime,
    ) -> bool:
        cursor.execute(
            "UPDATE outbox_events SET publish_attempts = publish_attempts + 1, "
            "claim_token = ?, lease_until = ? WHERE id = ? AND published_at IS NULL "
            "AND (claim_token IS NULL OR lease_until <= ?)",
            (
                str(claim_token),
                _writer_timestamp(lease_until),
                str(event_id),
                _writer_timestamp(now),
            ),
        )
        return cursor.rowcount == 1

    def release_for_retry(
        self,
        cursor: OutboxCursor,
        *,
        event_id: UUID,
        claim_token: UUID,
        available_at: datetime,
        now: datetime,
    ) -> bool:
        cursor.execute(
            "UPDATE outbox_events SET claim_token = NULL, lease_until = NULL, "
            "available_at = ? WHERE id = ? AND claim_token = ? "
            "AND lease_until > ? AND published_at IS NULL",
            (
                _writer_timestamp(available_at),
                str(event_id),
                str(claim_token),
                _writer_timestamp(now),
            ),
        )
        return cursor.rowcount == 1


class OutboxWriter:
    """Append and reserve events inside a caller-owned SQLite transaction."""

    def __init__(self, adapter: OutboxSqlAdapter | None = None) -> None:
        self._adapter = adapter or QmarkOutboxSqlAdapter()

    def enqueue(
        self,
        connection: OutboxConnection,
        *,
        tenant_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_version: int,
        event_type: str,
        payload: Mapping[str, object],
        created_at: datetime,
        available_at: datetime | None = None,
    ) -> _WriterOutboxEvent:
        """Write an event without committing the caller's transaction."""
        event = _WriterOutboxEvent(
            event_id=_writer_event_id(
                tenant_id, aggregate_type, aggregate_id, aggregate_version
            ),
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            event_type=event_type,
            payload=payload,
            created_at=created_at,
            available_at=available_at or created_at,
        )
        _writer_validate(event)
        cursor = connection.cursor()
        self._adapter.insert_event(cursor, event)
        row = self._adapter.find_event(
            cursor,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
        )
        if row is None:
            raise OutboxError("outbox event was not persisted")
        stored = _writer_row_event(row)
        if stored.immutable_identity() != event.immutable_identity():
            raise OutboxConflict("outbox replay conflicts with event")
        return stored

    def claim_available(
        self,
        connection: OutboxConnection,
        *,
        limit: int,
        now: datetime,
        lease_for: timedelta = _WRITER_DEFAULT_LEASE,
    ) -> tuple[_WriterOutboxEvent, ...]:
        """Reserve ready events without publishing or committing them."""
        if type(limit) is not int or limit <= 0:
            raise OutboxError("outbox claim limit must be positive")
        if not timedelta(0) < lease_for <= _WRITER_MAX_LEASE:
            raise OutboxError("outbox lease duration must be positive and bounded")
        _writer_timestamp(now)
        cursor = connection.cursor()
        claimed: list[_WriterOutboxEvent] = []
        for row in self._adapter.find_available(cursor, limit=limit, now=now):
            event = _writer_row_event(row)
            claim_token = uuid4()
            lease_until = now + lease_for
            if self._adapter.reserve(
                cursor,
                event_id=event.event_id,
                claim_token=claim_token,
                lease_until=lease_until,
                now=now,
            ):
                claimed.append(
                    replace(
                        event,
                        publish_attempts=event.publish_attempts + 1,
                        claim_token=claim_token,
                        lease_until=lease_until,
                    )
                )
        return tuple(claimed)

    def release_for_retry(
        self,
        connection: OutboxConnection,
        *,
        event_id: UUID,
        claim_token: UUID,
        now: datetime,
        retry_after: timedelta,
    ) -> bool:
        """Release one live worker claim and delay its next dispatch."""
        if not isinstance(event_id, UUID) or not isinstance(claim_token, UUID):
            raise OutboxError("outbox claim identity is invalid")
        if not timedelta(0) < retry_after <= _WRITER_MAX_RETRY_DELAY:
            raise OutboxError("outbox retry delay must be positive and bounded")
        _writer_timestamp(now)
        return self._adapter.release_for_retry(
            connection.cursor(),
            event_id=event_id,
            claim_token=claim_token,
            available_at=now + retry_after,
            now=now,
        )


__all__ = [
    "OutboxConnection",
    "OutboxConflict",
    "OutboxCursor",
    "OutboxError",
    "OutboxEvent",
    "OutboxIntegrityError",
    "OutboxNotFound",
    "OutboxSqlAdapter",
    "OutboxWriter",
    "PostgresOutbox",
    "QmarkOutboxSqlAdapter",
]
