"""BDD coverage for forward-only database migration execution."""

from __future__ import annotations

import sqlite3
from typing import Any

from pytest_bdd import given, scenarios, then, when

from poddown.migrations import Migration, MigrationOrderError, run_migrations

scenarios("../features/migrations.feature")


@given("an empty database and two ordered migrations")
def empty_database_with_migrations(context: Any) -> None:
    context.values["connection"] = sqlite3.connect(":memory:")
    context.values["migrations"] = (
        Migration("001", "a" * 64, ("CREATE TABLE first_change (id INTEGER)",)),
        Migration("002", "b" * 64, ("CREATE TABLE second_change (id INTEGER)",)),
    )


@when("I run the migrations twice")
def run_twice(context: Any) -> None:
    connection = context.values["connection"]
    migrations = context.values["migrations"]
    run_migrations(connection, migrations)
    run_migrations(connection, migrations)


@given("a migration ledger with history out of order")
def ledger_with_history_out_of_order(context: Any) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_migrations ("
        "migration_id TEXT PRIMARY KEY, "
        "checksum TEXT NOT NULL, "
        "applied_order INTEGER NOT NULL UNIQUE, "
        "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.executemany(
        "INSERT INTO schema_migrations "
        "(migration_id, checksum, applied_order) VALUES (?, ?, ?)",
        [("002", "b" * 64, 1), ("001", "a" * 64, 2)],
    )
    connection.commit()
    context.values["connection"] = connection
    context.values["migrations"] = (
        Migration("001", "a" * 64, ("CREATE TABLE blocked_change (id INTEGER)",)),
        Migration("002", "b" * 64, ()),
    )


@when("I run migrations against the ledger")
def run_against_ledger(context: Any) -> None:
    try:
        run_migrations(context.values["connection"], context.values["migrations"])
    except MigrationOrderError as error:
        context.values["migration_error"] = error


@then("migration execution fails without schema changes")
def migration_execution_fails_without_schema_changes(context: Any) -> None:
    connection = context.values["connection"]
    assert isinstance(context.values.get("migration_error"), MigrationOrderError)
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'blocked_change'"
        ).fetchall()
        == []
    )
    connection.close()


@then("each migration is recorded once and its schema change remains")
def each_migration_is_recorded_once(context: Any) -> None:
    connection = context.values["connection"]
    migration_rows = connection.execute(
        "SELECT migration_id FROM schema_migrations ORDER BY migration_id"
    ).fetchall()
    schema_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()

    assert migration_rows == [("001",), ("002",)]
    assert schema_rows == [
        ("first_change",),
        ("schema_migrations",),
        ("second_change",),
    ]
    connection.close()
