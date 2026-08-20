"""Forward-only migration execution for SQLite and PostgreSQL DB-API connections."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


class Cursor(Protocol):
    """Minimal cursor protocol required by the migration runner."""

    def execute(
        self, operation: str, parameters: Sequence[object] | None = None
    ) -> object:
        """Execute one SQL operation."""

    def fetchall(self) -> Sequence[Sequence[object]]:
        """Return all rows from the preceding query."""


class Connection(Protocol):
    """Minimal transactional DB-API connection protocol."""

    def cursor(self) -> Cursor:
        """Create a cursor."""

    def commit(self) -> None:
        """Commit the current transaction."""

    def rollback(self) -> None:
        """Roll back the current transaction."""


DatabaseKind = Literal["sqlite", "postgresql"]


class MigrationError(ValueError):
    """Base error for invalid migration state."""


class DuplicateMigrationId(MigrationError):
    """Raised when a migration catalog repeats an ID."""


class MigrationOrderError(MigrationError):
    """Raised when a catalog or its applied history is not strictly ordered."""


class MigrationChecksumDrift(MigrationError):
    """Raised when an applied migration's checksum differs from its catalog entry."""


@dataclass(frozen=True)
class Migration:
    """One immutable, ordered schema migration definition."""

    migration_id: str
    checksum: str
    statements: tuple[str, ...]


def run_migrations(connection: Connection, migrations: Sequence[Migration]) -> None:
    """Atomically validate and apply the ordered catalog once.

    SQLite acquires its database write lock with ``BEGIN IMMEDIATE``. PostgreSQL
    uses a transaction-scoped advisory lock before creating or reading the
    ledger. Therefore ledger creation, validation, application, and recording
    share one transaction with no separately committed setup boundary.
    """
    ordered_migrations = tuple(migrations)
    _validate_catalog(ordered_migrations)
    database_kind = _database_kind(connection)

    def operation(cursor: Cursor) -> None:
        _create_migration_ledger(cursor)
        _validate_migration_ledger(cursor, database_kind)
        applied = _load_applied_migrations(cursor)
        _validate_applied_history(ordered_migrations, applied)
        for applied_order, migration in enumerate(
            ordered_migrations[len(applied) :], start=len(applied) + 1
        ):
            _apply_migration(cursor, database_kind, migration, applied_order)

    _run_transaction(connection, database_kind, operation)


def _validate_catalog(migrations: tuple[Migration, ...]) -> None:
    previous_id: str | None = None
    for migration in migrations:
        if previous_id == migration.migration_id:
            raise DuplicateMigrationId(
                f"migration ID {migration.migration_id!r} is defined more than once"
            )
        if previous_id is not None and migration.migration_id < previous_id:
            raise MigrationOrderError(
                "migration IDs must be strictly increasing in catalog order"
            )
        previous_id = migration.migration_id


def _database_kind(connection: Connection) -> DatabaseKind:
    if isinstance(connection, sqlite3.Connection):
        return "sqlite"
    if connection.__class__.__module__.startswith("psycopg"):
        return "postgresql"
    raise MigrationError("unsupported DB-API connection for serialized migrations")


def _create_migration_ledger(cursor: Cursor) -> None:
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "migration_id TEXT PRIMARY KEY, "
        "checksum TEXT NOT NULL, "
        "applied_order INTEGER NOT NULL UNIQUE, "
        "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )


def _validate_migration_ledger(cursor: Cursor, database_kind: DatabaseKind) -> None:
    if database_kind == "sqlite":
        cursor.execute("PRAGMA table_info(schema_migrations)")
        columns = cursor.fetchall()
        expected_columns = ("migration_id", "checksum", "applied_order", "applied_at")
        actual_columns = tuple(str(column[1]) for column in columns)
        primary_key_columns = tuple(
            str(column[1]) for column in columns if _metadata_int(column[5]) > 0
        )
        required_columns = {
            str(column[1]): _metadata_int(column[3]) for column in columns
        }
        cursor.execute("PRAGMA index_list(schema_migrations)")
        indexes = cursor.fetchall()
        applied_order_is_unique = _sqlite_column_is_unique(cursor, indexes)
        if (
            actual_columns != expected_columns
            or primary_key_columns != ("migration_id",)
            or required_columns.get("checksum") != 1
            or required_columns.get("applied_order") != 1
            or required_columns.get("applied_at") != 1
            or not applied_order_is_unique
        ):
            raise MigrationError("incompatible schema_migrations table")
        return

    cursor.execute(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s "
        "ORDER BY ordinal_position",
        ("schema_migrations",),
    )
    columns = cursor.fetchall()
    cursor.execute(
        "SELECT attribute.attname FROM pg_constraint constraint_row "
        "JOIN pg_class relation ON relation.oid = constraint_row.conrelid "
        "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
        "JOIN unnest(constraint_row.conkey) WITH ORDINALITY AS key(column_number, "
        "position) "
        "ON TRUE JOIN pg_attribute attribute "
        "ON attribute.attrelid = relation.oid AND attribute.attnum = key.column_number "
        "WHERE constraint_row.contype = 'p' AND namespace.nspname = current_schema() "
        "AND relation.relname = %s ORDER BY key.position",
        ("schema_migrations",),
    )
    primary_key_columns = tuple(str(row[0]) for row in cursor.fetchall())
    expected_columns = ("migration_id", "checksum", "applied_order", "applied_at")
    if (
        tuple(str(column[0]) for column in columns) != expected_columns
        or primary_key_columns != ("migration_id",)
        or any(str(column[1]) != "NO" for column in columns[1:])
    ):
        raise MigrationError("incompatible schema_migrations table")


def _sqlite_column_is_unique(
    cursor: Cursor, indexes: Sequence[Sequence[object]]
) -> bool:
    for index in indexes:
        if _metadata_int(index[2]) != 1:
            continue
        cursor.execute("SELECT name FROM pragma_index_info(?)", (str(index[1]),))
        columns = cursor.fetchall()
        if tuple(str(column[0]) for column in columns) == ("applied_order",):
            return True
    return False


def _metadata_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise MigrationError("incompatible schema_migrations table")


def _load_applied_migrations(cursor: Cursor) -> tuple[tuple[str, str, int], ...]:
    cursor.execute(
        "SELECT migration_id, checksum, applied_order FROM schema_migrations "
        "ORDER BY applied_order"
    )
    return tuple(
        (str(row[0]), str(row[1]), _metadata_int(row[2])) for row in cursor.fetchall()
    )


def _validate_applied_history(
    migrations: tuple[Migration, ...], applied: tuple[tuple[str, str, int], ...]
) -> None:
    applied_ids = tuple(migration_id for migration_id, _, _ in applied)
    expected_ids = tuple(
        migration.migration_id for migration in migrations[: len(applied)]
    )
    expected_orders = tuple(range(1, len(applied) + 1))
    actual_orders = tuple(applied_order for _, _, applied_order in applied)
    if applied_ids != expected_ids or actual_orders != expected_orders:
        raise MigrationOrderError(
            "applied migrations must be a contiguous ordered prefix of the catalog"
        )

    for migration, (_, checksum, _) in zip(
        migrations[: len(applied)], applied, strict=True
    ):
        if migration.checksum != checksum:
            raise MigrationChecksumDrift(
                f"checksum drift detected for migration {migration.migration_id!r}"
            )


def _apply_migration(
    cursor: Cursor,
    database_kind: DatabaseKind,
    migration: Migration,
    applied_order: int,
) -> None:
    for statement in migration.statements:
        cursor.execute(statement)
    placeholder = "?" if database_kind == "sqlite" else "%s"
    cursor.execute(
        "INSERT INTO schema_migrations "
        "(migration_id, checksum, applied_order) "
        f"VALUES ({placeholder}, {placeholder}, {placeholder})",
        (migration.migration_id, migration.checksum, applied_order),
    )


def _run_transaction(
    connection: Connection,
    database_kind: DatabaseKind,
    operation: Callable[[Cursor], None],
) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE" if database_kind == "sqlite" else "BEGIN")
        if database_kind == "postgresql":
            cursor.execute("SELECT pg_advisory_xact_lock(860812401)")
        operation(cursor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
