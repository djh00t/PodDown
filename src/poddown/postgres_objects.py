"""Tenant-scoped PostgreSQL object references and conservative orphan cleanup."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from poddown.object_storage import ObjectRef, ObjectValidationError


class ObjectReferenceError(ValueError):
    """Base error for durable object-reference failures."""


class ObjectReferenceConflict(ObjectReferenceError, RuntimeError):
    """A content-addressed object reference conflicts with durable metadata."""


class ObjectReferenceNotFound(ObjectReferenceError):
    """A scoped object reference does not exist."""


class PostgresObjectCursor(Protocol):
    """Minimal DB-API cursor surface for object references."""

    rowcount: int

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresObjectCursor:
        """Execute one parameterized statement."""

    def fetchone(self) -> Sequence[object] | None:
        """Fetch one row."""

    def fetchall(self) -> list[Sequence[object]]:
        """Fetch all rows."""


class PostgresObjectConnection(Protocol):
    """Minimal transaction surface for psycopg and test doubles."""

    def cursor(self) -> PostgresObjectCursor:
        """Return a transaction cursor."""

    def transaction(self) -> AbstractContextManager[object]:
        """Return a transaction context manager."""


ConnectionFactory = Callable[[], PostgresObjectConnection]


def _set_tenant(cursor: PostgresObjectCursor, tenant_id: UUID) -> None:
    cursor.execute(
        "SELECT set_config('poddown.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


def _row_ref(tenant_id: UUID, project_id: UUID, row: Sequence[object]) -> ObjectRef:
    if len(row) != 5:
        raise ObjectReferenceError("object reference row has an unexpected shape")
    sha256, storage_key, media_type, byte_count, name = row
    if not isinstance(sha256, str):
        raise ObjectReferenceError("persisted object reference metadata is invalid")
    if not isinstance(storage_key, str):
        raise ObjectReferenceError("persisted object reference metadata is invalid")
    if not isinstance(media_type, str):
        raise ObjectReferenceError("persisted object reference metadata is invalid")
    if not isinstance(name, str):
        raise ObjectReferenceError("persisted object reference metadata is invalid")
    if type(byte_count) is not int:
        raise ObjectReferenceError("persisted object reference byte count is invalid")
    try:
        return ObjectRef(
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            media_type=media_type,
            byte_count=byte_count,
            sha256=sha256,
            storage_key=storage_key,
        )
    except (ObjectValidationError, TypeError, ValueError) as error:
        raise ObjectReferenceError("persisted object reference is invalid") from error


def _same_reference(left: ObjectRef, right: ObjectRef) -> bool:
    return left.to_dict() == right.to_dict()


class PostgresObjectReferenceRepository:
    """Persist immutable object metadata under PostgreSQL tenant RLS."""

    _SELECT = (
        "SELECT sha256, storage_key, media_type, byte_count, name "
        "FROM object_references WHERE tenant_id = %s AND project_id = %s "
    )

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def record(self, reference: ObjectRef) -> ObjectRef:
        """Insert or replay one exact content-addressed reference."""
        if not isinstance(reference, ObjectRef):
            raise ObjectReferenceError("reference must be an ObjectRef")
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, reference.tenant_id)
                existing = self._get(cursor, reference)
                if existing is not None:
                    if not _same_reference(existing, reference):
                        raise ObjectReferenceConflict("object reference conflicts")
                    return existing
                cursor.execute(
                    """INSERT INTO object_references (
                        tenant_id, project_id, sha256, storage_key, media_type,
                        byte_count, name
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, project_id, sha256) DO NOTHING""",
                    (
                        str(reference.tenant_id),
                        str(reference.project_id),
                        reference.sha256,
                        reference.storage_key,
                        reference.media_type,
                        reference.byte_count,
                        reference.name,
                    ),
                )
                inserted = self._get(cursor, reference)
                if inserted is None:
                    raise ObjectReferenceConflict("object reference was not persisted")
                if not _same_reference(inserted, reference):
                    raise ObjectReferenceConflict("object reference conflicts")
                return inserted
        finally:
            _close(connection)

    def get(self, tenant_id: UUID, project_id: UUID, sha256: str) -> ObjectRef:
        """Read one reference within explicit tenant/project scope."""
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                cursor.execute(
                    self._SELECT + "AND sha256 = %s",
                    (str(tenant_id), str(project_id), sha256),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ObjectReferenceNotFound("object reference was not found")
                return _row_ref(tenant_id, project_id, row)
        finally:
            _close(connection)

    def list_for_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
    ) -> tuple[ObjectRef, ...]:
        """List immutable references in deterministic checksum order."""
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                cursor.execute(
                    self._SELECT + "ORDER BY sha256",
                    (str(tenant_id), str(project_id)),
                )
                return tuple(
                    _row_ref(tenant_id, project_id, row) for row in cursor.fetchall()
                )
        finally:
            _close(connection)

    def remove(self, reference: ObjectRef) -> None:
        """Remove one reference only after the caller has deleted the object."""
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, reference.tenant_id)
                cursor.execute(
                    "DELETE FROM object_references "
                    "WHERE tenant_id = %s AND project_id = %s AND sha256 = %s",
                    (
                        str(reference.tenant_id),
                        str(reference.project_id),
                        reference.sha256,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ObjectReferenceNotFound("object reference was not found")
        finally:
            _close(connection)

    def _get(
        self,
        cursor: PostgresObjectCursor,
        reference: ObjectRef,
    ) -> ObjectRef | None:
        cursor.execute(
            self._SELECT + "AND sha256 = %s",
            (
                str(reference.tenant_id),
                str(reference.project_id),
                reference.sha256,
            ),
        )
        row = cursor.fetchone()
        return (
            _row_ref(reference.tenant_id, reference.project_id, row)
            if row is not None
            else None
        )


@dataclass(frozen=True, slots=True)
class InventoryObject:
    """One storage inventory entry eligible for reference reconciliation."""

    storage_key: str
    discovered_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.storage_key, str) or not self.storage_key.strip():
            raise ValueError("storage_key must be non-empty")
        if (
            not isinstance(self.discovered_at, datetime)
            or self.discovered_at.tzinfo is None
            or self.discovered_at.utcoffset() is None
        ):
            raise ValueError("discovered_at must be timezone-aware")


def _row_inventory(row: Sequence[object]) -> InventoryObject:
    """Reconstruct one validated inventory observation from PostgreSQL."""
    if len(row) != 2 or not isinstance(row[0], str) or not isinstance(row[1], datetime):
        raise ObjectReferenceError("object inventory row has an unexpected shape")
    try:
        return InventoryObject(row[0], row[1])
    except (TypeError, ValueError) as error:
        raise ObjectReferenceError("object inventory row is invalid") from error


class PostgresObjectInventoryRepository:
    """Persist first-seen storage inventory observations under tenant RLS."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def observe(
        self,
        tenant_id: UUID,
        project_id: UUID,
        storage_keys: Sequence[str],
        *,
        observed_at: datetime,
    ) -> tuple[InventoryObject, ...]:
        """Record an inventory pass while retaining each key's first sighting."""
        if (
            not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            raise ObjectReferenceError("observed_at must be timezone-aware")
        keys = tuple(sorted(set(storage_keys)))
        if any(not isinstance(key, str) or not key.strip() for key in keys):
            raise ObjectReferenceError("storage inventory keys must be non-empty")
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                for storage_key in keys:
                    cursor.execute(
                        """INSERT INTO object_inventory (
                            tenant_id, project_id, storage_key,
                            first_seen_at, last_seen_at
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, project_id, storage_key)
                        DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at""",
                        (
                            str(tenant_id),
                            str(project_id),
                            storage_key,
                            observed_at,
                            observed_at,
                        ),
                    )
                observations = {
                    item.storage_key: item
                    for item in self._list(cursor, tenant_id, project_id)
                }
                return tuple(observations[key] for key in keys)
        finally:
            _close(connection)

    def list_for_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
    ) -> tuple[InventoryObject, ...]:
        """List observations in deterministic storage-key order."""
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                return self._list(cursor, tenant_id, project_id)
        finally:
            _close(connection)

    def remove(self, tenant_id: UUID, project_id: UUID, storage_key: str) -> None:
        """Forget one key only after its storage deletion succeeds."""
        if not isinstance(storage_key, str) or not storage_key.strip():
            raise ObjectReferenceError("storage_key must be non-empty")
        connection = self._connection_factory()
        try:
            with connection.transaction():
                cursor = connection.cursor()
                _set_tenant(cursor, tenant_id)
                cursor.execute(
                    "DELETE FROM object_inventory "
                    "WHERE tenant_id = %s AND project_id = %s AND storage_key = %s",
                    (str(tenant_id), str(project_id), storage_key),
                )
                if cursor.rowcount != 1:
                    raise ObjectReferenceNotFound("object inventory was not found")
        finally:
            _close(connection)

    @staticmethod
    def _list(
        cursor: PostgresObjectCursor,
        tenant_id: UUID,
        project_id: UUID,
    ) -> tuple[InventoryObject, ...]:
        cursor.execute(
            "SELECT storage_key, first_seen_at FROM object_inventory "
            "WHERE tenant_id = %s AND project_id = %s ORDER BY storage_key",
            (str(tenant_id), str(project_id)),
        )
        return tuple(_row_inventory(row) for row in cursor.fetchall())


@dataclass(frozen=True, slots=True)
class OrphanCollectionReport:
    """Safe two-boundary garbage-collection result."""

    discovered_keys: tuple[str, ...]
    deleted_keys: tuple[str, ...]
    retained_keys: tuple[str, ...]


class OrphanGarbageCollector:
    """Delete only unreferenced inventory entries older than a grace period."""

    def collect(
        self,
        inventory: Sequence[InventoryObject],
        referenced_keys: set[str],
        *,
        now: datetime,
        grace_period: timedelta,
        delete: Callable[[str], None],
    ) -> OrphanCollectionReport:
        """Discover candidates and delete only stale, unreferenced keys."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if grace_period < timedelta(0):
            raise ValueError("grace_period must be non-negative")
        discovered = tuple(item.storage_key for item in inventory)
        deleted: list[str] = []
        retained: list[str] = []
        cutoff = now.astimezone(UTC) - grace_period
        for item in inventory:
            if item.storage_key in referenced_keys or item.discovered_at > cutoff:
                retained.append(item.storage_key)
                continue
            delete(item.storage_key)
            deleted.append(item.storage_key)
        return OrphanCollectionReport(
            tuple(discovered), tuple(deleted), tuple(retained)
        )


class ObjectReferenceListing(Protocol):
    """Reference repository surface required by scoped cleanup."""

    def list_for_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
    ) -> tuple[ObjectRef, ...]:
        """List references in one tenant/project scope."""


class ObjectInventoryRepository(Protocol):
    """Inventory repository surface required by scoped cleanup."""

    def observe(
        self,
        tenant_id: UUID,
        project_id: UUID,
        storage_keys: Sequence[str],
        *,
        observed_at: datetime,
    ) -> tuple[InventoryObject, ...]:
        """Record one provider inventory pass."""

    def remove(self, tenant_id: UUID, project_id: UUID, storage_key: str) -> None:
        """Remove an observation after successful storage deletion."""


class OrphanCleanupService:
    """Join a current storage inventory to durable references before deletion."""

    def __init__(
        self,
        *,
        references: ObjectReferenceListing,
        inventory: ObjectInventoryRepository,
        collector: OrphanGarbageCollector | None = None,
    ) -> None:
        if not hasattr(references, "list_for_project"):
            raise TypeError("references must list project references")
        if not hasattr(inventory, "observe") or not hasattr(inventory, "remove"):
            raise TypeError("inventory must observe and remove keys")
        self._references = references
        self._inventory = inventory
        self._collector = collector or OrphanGarbageCollector()

    def collect_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
        storage_keys: Sequence[str],
        *,
        now: datetime,
        grace_period: timedelta,
        delete: Callable[[str], None],
        observed_at: datetime,
    ) -> OrphanCollectionReport:
        """Collect only current, stale, unreferenced keys in one project."""
        inventory = self._inventory.observe(
            tenant_id,
            project_id,
            storage_keys,
            observed_at=observed_at,
        )
        referenced_keys = {
            reference.storage_key
            for reference in self._references.list_for_project(tenant_id, project_id)
        }
        report = self._collector.collect(
            inventory,
            referenced_keys,
            now=now,
            grace_period=grace_period,
            delete=delete,
        )
        for storage_key in report.deleted_keys:
            self._inventory.remove(tenant_id, project_id, storage_key)
        return report


__all__ = [
    "ConnectionFactory",
    "InventoryObject",
    "ObjectInventoryRepository",
    "ObjectReferenceListing",
    "ObjectReferenceConflict",
    "ObjectReferenceError",
    "ObjectReferenceNotFound",
    "PostgresObjectInventoryRepository",
    "OrphanCollectionReport",
    "OrphanCleanupService",
    "OrphanGarbageCollector",
    "PostgresObjectConnection",
    "PostgresObjectCursor",
    "PostgresObjectReferenceRepository",
]
