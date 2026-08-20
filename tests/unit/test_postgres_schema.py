"""Unit tests for forward-only PostgreSQL migrations and tenant context."""

from __future__ import annotations

from uuid import UUID

import pytest

from poddown.postgres_schema import (
    MIGRATIONS,
    Migration,
    MigrationRunner,
    set_transaction_tenant,
)


class _Cursor:
    def __init__(self, applied: set[int]) -> None:
        self.applied = applied
        self.rows: list[tuple[int, str]] = []

    def execute(self, sql: str, params=()):
        if sql.startswith("SELECT version, name"):
            self.rows = [(version, str(version)) for version in sorted(self.applied)]
        elif sql.startswith("INSERT INTO poddown_schema_migrations"):
            self.applied.add(params[0])
        return self

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self) -> None:
        self.applied: set[int] = set()
        self.cursor_instance = _Cursor(self.applied)

    def cursor(self):
        return self.cursor_instance

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_migration_catalog_has_unique_forward_versions() -> None:
    versions = [migration.version for migration in MIGRATIONS]

    assert versions == sorted(set(versions))
    assert versions[-7:] == [21, 22, 23, 24, 25, 26, 27]


def test_runner_rejects_duplicate_migration_versions() -> None:
    duplicate = Migration(1, "duplicate", "SELECT 1")

    with pytest.raises(ValueError, match="unique"):
        MigrationRunner((*MIGRATIONS, duplicate))


def test_tenant_context_requires_uuidv7() -> None:
    with pytest.raises(ValueError, match="UUIDv7"):
        set_transaction_tenant(
            _Connection(), UUID("00000000-0000-4000-8000-000000000000")
        )


def test_legacy_backfills_preserve_episode_bytes_and_safe_object_names() -> None:
    episode_migration = next(
        migration for migration in MIGRATIONS if migration.version == 18
    )
    object_migration = next(
        migration for migration in MIGRATIONS if migration.version == 23
    )

    assert "source_content = source_bytes" in episode_migration.sql
    assert "source_byte_count = octet_length(source_content)" in episode_migration.sql
    assert "request_fingerprint = source_sha256" in episode_migration.sql
    assert "name = storage_key" not in object_migration.sql
    assert "legacy-" in object_migration.sql

    outbox_migration = next(
        migration for migration in MIGRATIONS if migration.version == 24
    )
    assert "outbox_events AS outbox SET project_id" in outbox_migration.sql
    assert "ALTER TABLE outbox_events ALTER COLUMN project_id SET NOT NULL" in (
        outbox_migration.sql
    )

    publication_migration = next(
        migration for migration in MIGRATIONS if migration.version == 25
    )
    assert "episode_version_id" in publication_migration.sql
    assert "disclosure" in publication_migration.sql
    assert "ALTER TABLE publication_receipts ALTER COLUMN disclosure SET NOT NULL" in (
        publication_migration.sql
    )
