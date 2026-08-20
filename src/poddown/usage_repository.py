"""Durable DB-API repository for immutable provider usage evidence."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Protocol
from uuid import UUID

from uuid6 import uuid7

from poddown.persistence import UsageEvent
from poddown.tenant_context import tenant_transaction


class UsageCursor(Protocol):
    """Minimal DB-API cursor required by the usage ledger repository."""

    def execute(self, operation: str, parameters: Sequence[object] = ()) -> object: ...

    def fetchone(self) -> Mapping[str, object] | Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Mapping[str, object] | Sequence[object]]: ...


class UsageConnection(Protocol):
    """Minimal transactional DB-API connection required by the repository."""

    def cursor(self) -> UsageCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class UsageRepositoryError(ValueError):
    """Base error for durable usage ledger failures."""


class UsageReplayConflict(UsageRepositoryError):
    """A provider request was rebound to different immutable evidence."""


class UsageEventUnavailable(UsageRepositoryError):
    """A usage event is absent from the caller's tenant scope."""


class UsageLedgerPersistenceError(UsageRepositoryError):
    """Usage evidence could not be safely persisted or replayed."""


_SELECT = (
    "SELECT tenant_id, project_id, job_id, provider, provider_request_id, operation, "
    "usage, currency, estimated_cost, reconciled_cost, occurred_at FROM usage_events "
)


def _require_provider(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("provider is invalid")
    return value


def _require_uuid7(value: object, field: str) -> UUID:
    if not isinstance(value, UUID) or value.version != 7:
        raise ValueError(f"{field} must be a UUIDv7")
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.isoformat()


def _row_value(
    row: Mapping[str, object] | Sequence[object], index: int, key: str
) -> object:
    return row[key] if isinstance(row, Mapping) else row[index]


def _placeholder(connection: UsageConnection) -> str:
    """Return the DB-API parameter marker for the active connection."""
    return "?" if isinstance(connection, sqlite3.Connection) else "%s"


def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


def _require_job_scope(
    cursor: UsageCursor, event: UsageEvent, placeholder: str
) -> None:
    """Require a job to belong to the event project within its tenant."""
    cursor.execute(
        "SELECT 1 FROM render_jobs WHERE tenant_id = "
        f"{placeholder} AND project_id = {placeholder} AND id = {placeholder}",
        (str(event.tenant_id), str(event.project_id), str(event.job_id)),
    )
    if cursor.fetchone() is None:
        raise UsageEventUnavailable("job is unavailable for project")


def _is_integrity_error(error: Exception) -> bool:
    """Recognize supported DB-API uniqueness errors without loading psycopg."""
    if isinstance(error, sqlite3.IntegrityError):
        return True
    return any(
        error_type.__name__ == "IntegrityError"
        and error_type.__module__.startswith("psycopg")
        for error_type in type(error).__mro__
    )


def _usage_from_row(
    row: Mapping[str, object] | Sequence[object],
) -> tuple[str, UsageEvent]:
    try:
        provider = _require_provider(_row_value(row, 3, "provider"))
        units_value = json.loads(str(_row_value(row, 6, "usage")))
        if not isinstance(units_value, dict):
            raise ValueError("usage units must be an object")
        event = UsageEvent(
            tenant_id=UUID(str(_row_value(row, 0, "tenant_id"))),
            project_id=UUID(str(_row_value(row, 1, "project_id"))),
            job_id=UUID(str(_row_value(row, 2, "job_id"))),
            provider_request_id=str(_row_value(row, 4, "provider_request_id")),
            operation=str(_row_value(row, 5, "operation")),
            units=units_value,
            currency=str(_row_value(row, 7, "currency")),
            estimated_cost=Decimal(str(_row_value(row, 8, "estimated_cost"))),
            reconciled_cost=(
                Decimal(str(_row_value(row, 9, "reconciled_cost")))
                if _row_value(row, 9, "reconciled_cost") is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(_row_value(row, 10, "occurred_at"))),
        )
        return provider, event
    except (
        DecimalException,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise UsageLedgerPersistenceError("persisted usage event is invalid") from error


def _same_identity(
    stored_provider: str, stored_event: UsageEvent, provider: str, event: UsageEvent
) -> bool:
    return stored_provider == provider and stored_event == event


class UsageLedgerRepository:
    """Append or replay tenant-scoped provider usage without changing cost evidence."""

    def __init__(self, connection_factory: Callable[[], UsageConnection]) -> None:
        self._connection_factory = connection_factory

    def record_or_replay(self, *, provider: str, event: UsageEvent) -> UsageEvent:
        """Append one provider request or return its exact immutable replay."""
        _require_provider(provider)
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise UsageLedgerPersistenceError(
                "usage ledger persistence failed"
            ) from error
        placeholder = _placeholder(connection)
        predicate = (str(event.tenant_id), provider, event.provider_request_id)
        try:
            with tenant_transaction(connection, event.tenant_id) as cursor:
                _require_job_scope(cursor, event, placeholder)
                cursor.execute(
                    _SELECT + "WHERE tenant_id = "
                    f"{placeholder} AND provider = {placeholder} "
                    f"AND provider_request_id = {placeholder}",
                    predicate,
                )
                row = cursor.fetchone()
                if row is not None:
                    stored_provider, stored_event = _usage_from_row(row)
                    if not _same_identity(
                        stored_provider, stored_event, provider, event
                    ):
                        raise UsageReplayConflict("usage replay conflicts with receipt")
                    return stored_event
                cursor.execute(
                    "INSERT INTO usage_events (id, tenant_id, project_id, job_id, "
                    "provider, provider_request_id, operation, unit_type, units, "
                    "usage, "
                    "currency, estimated_cost, reconciled_cost, occurred_at) "
                    f"VALUES ({', '.join((placeholder,) * 14)})",
                    (
                        str(uuid7()),
                        str(event.tenant_id),
                        str(event.project_id),
                        str(event.job_id),
                        provider,
                        event.provider_request_id,
                        event.operation,
                        next(iter(sorted(event.units))),
                        event.units[next(iter(sorted(event.units)))],
                        json.dumps(
                            dict(event.units), separators=(",", ":"), sort_keys=True
                        ),
                        event.currency,
                        format(event.estimated_cost, "f"),
                        format(event.reconciled_cost, "f")
                        if event.reconciled_cost is not None
                        else None,
                        _timestamp(event.created_at),
                    ),
                )
                return event
        except UsageRepositoryError:
            raise
        except Exception as error:
            if not _is_integrity_error(error):
                raise UsageLedgerPersistenceError(
                    "usage ledger persistence failed"
                ) from error
            try:
                with tenant_transaction(connection, event.tenant_id) as cursor:
                    _require_job_scope(cursor, event, placeholder)
                    cursor.execute(
                        _SELECT + "WHERE tenant_id = "
                        f"{placeholder} AND provider = {placeholder} "
                        f"AND provider_request_id = {placeholder}",
                        predicate,
                    )
                    row = cursor.fetchone()
            except Exception as reread_error:
                raise UsageLedgerPersistenceError(
                    "usage ledger persistence failed"
                ) from reread_error
            if row is None:
                raise UsageReplayConflict(
                    "usage replay conflicts with receipt"
                ) from error
            stored_provider, stored_event = _usage_from_row(row)
            if not _same_identity(stored_provider, stored_event, provider, event):
                raise UsageReplayConflict(
                    "usage replay conflicts with receipt"
                ) from error
            return stored_event
        finally:
            _close(connection)

    def get(
        self, *, tenant_id: UUID, provider: str, provider_request_id: str
    ) -> UsageEvent:
        """Read one provider request within the caller's tenant scope."""
        _require_uuid7(tenant_id, "tenant_id")
        _require_provider(provider)
        if not isinstance(provider_request_id, str) or not provider_request_id.strip():
            raise ValueError("provider_request_id is invalid")
        try:
            connection = self._connection_factory()
            placeholder = _placeholder(connection)
            with tenant_transaction(connection, tenant_id) as cursor:
                cursor.execute(
                    _SELECT + "WHERE tenant_id = "
                    f"{placeholder} AND provider = {placeholder} "
                    f"AND provider_request_id = {placeholder}",
                    (str(tenant_id), provider, provider_request_id),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise UsageLedgerPersistenceError(
                "usage ledger persistence failed"
            ) from error
        finally:
            _close(connection)
        if row is None:
            raise UsageEventUnavailable("usage event is unavailable")
        _, event = _usage_from_row(row)
        return event

    def list_for_job(
        self, *, tenant_id: UUID, project_id: UUID, job_id: UUID
    ) -> tuple[UsageEvent, ...]:
        """List provider usage in stable order for one tenant/project/job scope."""
        _require_uuid7(tenant_id, "tenant_id")
        _require_uuid7(project_id, "project_id")
        _require_uuid7(job_id, "job_id")
        try:
            connection = self._connection_factory()
            placeholder = _placeholder(connection)
            with tenant_transaction(connection, tenant_id) as cursor:
                cursor.execute(
                    _SELECT + "WHERE tenant_id = "
                    f"{placeholder} AND project_id = {placeholder} "
                    f"AND job_id = {placeholder} "
                    "ORDER BY occurred_at, provider, provider_request_id",
                    (str(tenant_id), str(project_id), str(job_id)),
                )
                rows = cursor.fetchall()
        except Exception as error:
            raise UsageLedgerPersistenceError(
                "usage ledger persistence failed"
            ) from error
        finally:
            _close(connection)
        return tuple(_usage_from_row(row)[1] for row in rows)


__all__ = [
    "UsageEventUnavailable",
    "UsageLedgerPersistenceError",
    "UsageLedgerRepository",
    "UsageReplayConflict",
    "UsageRepositoryError",
]
