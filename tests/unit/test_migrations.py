"""Unit contracts for the driver-agnostic migration runner."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from poddown.migrations import (
    DuplicateMigrationId,
    Migration,
    MigrationChecksumDrift,
    MigrationError,
    MigrationOrderError,
    run_migrations,
)


def test_runner_commits_each_unapplied_migration_once() -> None:
    connection = sqlite3.connect(":memory:")
    migrations = (
        Migration(
            "001", "a" * 64, ("CREATE TABLE events (migration_id TEXT PRIMARY KEY)",)
        ),
        Migration("002", "b" * 64, ("INSERT INTO events VALUES ('002')",)),
    )

    run_migrations(connection, migrations)
    run_migrations(connection, migrations)

    assert connection.execute("SELECT migration_id FROM events").fetchall() == [
        ("002",)
    ]
    assert connection.execute(
        "SELECT migration_id, checksum FROM schema_migrations ORDER BY migration_id"
    ).fetchall() == [("001", "a" * 64), ("002", "b" * 64)]
    connection.close()


def test_runner_rejects_duplicate_migration_ids_before_schema_mutation() -> None:
    connection = sqlite3.connect(":memory:")
    migrations = (
        Migration("001", "a" * 64, ("CREATE TABLE should_not_exist (id INTEGER)",)),
        Migration("001", "b" * 64, ("CREATE TABLE also_not_exist (id INTEGER)",)),
    )

    with pytest.raises(DuplicateMigrationId):
        run_migrations(connection, migrations)

    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'schema_migrations'"
        ).fetchall()
        == []
    )
    connection.close()


def test_runner_rejects_non_increasing_migration_ids_before_schema_mutation() -> None:
    connection = sqlite3.connect(":memory:")
    migrations = (
        Migration("002", "b" * 64, ("CREATE TABLE should_not_exist (id INTEGER)",)),
        Migration("001", "a" * 64, ("CREATE TABLE also_not_exist (id INTEGER)",)),
    )

    with pytest.raises(MigrationOrderError):
        run_migrations(connection, migrations)

    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'schema_migrations'"
        ).fetchall()
        == []
    )
    connection.close()


def test_runner_rejects_checksum_drift_without_applying_later_migrations() -> None:
    connection = sqlite3.connect(":memory:")
    original = Migration("001", "a" * 64, ("CREATE TABLE stable_change (id INTEGER)",))
    run_migrations(connection, (original,))

    with pytest.raises(MigrationChecksumDrift):
        run_migrations(
            connection,
            (
                Migration("001", "c" * 64, original.statements),
                Migration("002", "b" * 64, ("CREATE TABLE later_change (id INTEGER)",)),
            ),
        )

    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'later_change'"
        ).fetchall()
        == []
    )
    connection.close()


def test_runner_rejects_history_recorded_out_of_catalog_order() -> None:
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
    migrations = (
        Migration("001", "a" * 64, ("CREATE TABLE should_not_exist (id INTEGER)",)),
        Migration("002", "b" * 64, ()),
    )

    with pytest.raises(MigrationOrderError):
        run_migrations(connection, migrations)

    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'should_not_exist'"
        ).fetchall()
        == []
    )
    connection.close()


def test_runner_rejects_existing_ledger_without_migration_id_primary_key() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_migrations ("
        "migration_id TEXT NOT NULL, "
        "checksum TEXT NOT NULL, "
        "applied_order INTEGER NOT NULL UNIQUE, "
        "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.commit()

    with pytest.raises(MigrationError, match="incompatible schema_migrations table"):
        run_migrations(
            connection,
            (
                Migration(
                    "001", "a" * 64, ("CREATE TABLE should_not_exist (id INTEGER)",)
                ),
            ),
        )

    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'should_not_exist'"
        ).fetchall()
        == []
    )
    connection.close()


def test_runner_serializes_concurrent_sqlite_callers(tmp_path: object) -> None:
    database = str(tmp_path / "migrations.sqlite3")
    first_connection = sqlite3.connect(database, timeout=5.0, check_same_thread=False)
    second_connection = sqlite3.connect(database, timeout=5.0, check_same_thread=False)
    first_migration_started = threading.Event()
    release_first_migration = threading.Event()
    second_finished = threading.Event()
    failures: list[BaseException] = []

    def hold_first_migration() -> int:
        first_migration_started.set()
        assert release_first_migration.wait(timeout=5.0)
        return 1

    first_connection.create_function("hold_first_migration", 0, hold_first_migration)
    migrations = (
        Migration(
            "001",
            "a" * 64,
            (
                "SELECT hold_first_migration()",
                "CREATE TABLE migration_effects (migration_id TEXT PRIMARY KEY)",
                "INSERT INTO migration_effects VALUES ('001')",
            ),
        ),
    )

    def run_first() -> None:
        try:
            run_migrations(first_connection, migrations)
        except BaseException as error:
            failures.append(error)

    def run_second() -> None:
        try:
            run_migrations(second_connection, migrations)
        except BaseException as error:
            failures.append(error)
        finally:
            second_finished.set()

    first_thread = threading.Thread(target=run_first)
    second_thread = threading.Thread(target=run_second)
    first_thread.start()
    assert first_migration_started.wait(timeout=5.0)
    second_thread.start()
    assert not second_finished.wait(timeout=0.1)
    release_first_migration.set()
    first_thread.join(timeout=5.0)
    second_thread.join(timeout=5.0)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert failures == []
    reader = sqlite3.connect(database)
    try:
        assert reader.execute(
            "SELECT migration_id FROM migration_effects"
        ).fetchall() == [("001",)]
    finally:
        reader.close()
    first_connection.close()
    second_connection.close()


def test_runner_rolls_back_a_failed_migration_and_leaves_it_unrecorded() -> None:
    connection = sqlite3.connect(":memory:")
    migration = Migration(
        "001",
        "a" * 64,
        ("CREATE TABLE partial_change (id INTEGER)", "THIS IS NOT VALID SQL"),
    )

    with pytest.raises(sqlite3.OperationalError):
        run_migrations(connection, (migration,))

    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'partial_change'"
        ).fetchall()
        == []
    )
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'schema_migrations'"
        ).fetchall()
        == []
    )
    connection.close()
