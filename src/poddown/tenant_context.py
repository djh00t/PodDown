"""Transaction-scoped PostgreSQL tenant context for RLS-bound DB-API work."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Literal, Protocol, cast
from uuid import UUID

DatabaseKind = Literal["postgresql", "sqlite", "test"]


class TenantCursor(Protocol):
    """Minimal cursor protocol required by the tenant transaction boundary."""

    def execute(self, operation: str, parameters: Sequence[object] = ()) -> object:
        """Execute one parameterized DB-API operation."""


class TenantConnection[TenantCursorT: TenantCursor](Protocol):
    """Minimal transactional DB-API connection protocol."""

    def cursor(self) -> TenantCursorT:
        """Create a cursor for one transaction."""

    def commit(self) -> None:
        """Commit the current transaction."""

    def rollback(self) -> None:
        """Roll back the current transaction."""


class TenantContextError(ValueError):
    """Raised when a tenant transaction lacks a validated tenant identity."""


def _validate_tenant_id(tenant_id: object) -> UUID:
    if not isinstance(tenant_id, UUID) or tenant_id.version != 7:
        raise TenantContextError("tenant_id must be a UUIDv7")
    return tenant_id


def _database_kind(connection: object) -> DatabaseKind:
    """Infer only known DB-API connection kinds; unknown drivers fail closed."""
    configured_kind = getattr(connection, "poddown_database_kind", None)
    if configured_kind in {"postgresql", "sqlite", "test"}:
        return cast(DatabaseKind, configured_kind)
    if configured_kind is not None:
        raise TenantContextError("database kind is invalid")
    if isinstance(connection, sqlite3.Connection):
        return "sqlite"
    if type(connection).__module__.startswith("psycopg"):
        return "postgresql"
    raise TenantContextError("database kind is unresolved")


def _resolve_database_kind(
    connection: object, requested_kind: DatabaseKind | None
) -> DatabaseKind:
    """Resolve a kind without allowing callers to bypass connection checks."""
    if requested_kind is not None and requested_kind not in {
        "postgresql",
        "sqlite",
        "test",
    }:
        raise TenantContextError("database kind is invalid")
    inferred_kind = _database_kind(connection)
    if requested_kind is not None and requested_kind != inferred_kind:
        raise TenantContextError("database kind does not match connection")
    return inferred_kind


@contextmanager
def tenant_transaction[TenantCursorT: TenantCursor](
    connection: TenantConnection[TenantCursorT],
    tenant_id: object,
    *,
    database_kind: DatabaseKind | None = None,
) -> Iterator[TenantCursorT]:
    """Run DB-API work with a transaction-local PostgreSQL tenant context.

    PostgreSQL receives exactly one parameterized ``set_config`` call after the
    transaction begins. Its ``is_local`` argument makes the setting disappear
    on commit or rollback before a pooled connection can be reused. SQLite and
    test boundaries intentionally do not set PostgreSQL context and therefore
    do not provide evidence that RLS is active.
    """
    validated_tenant_id = _validate_tenant_id(tenant_id)
    resolved_database_kind = _resolve_database_kind(connection, database_kind)
    try:
        cursor = connection.cursor()
        if resolved_database_kind != "sqlite" or not getattr(
            connection, "in_transaction", False
        ):
            cursor.execute("BEGIN")
        if resolved_database_kind == "postgresql":
            cursor.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (str(validated_tenant_id),),
            )
        yield cursor
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
