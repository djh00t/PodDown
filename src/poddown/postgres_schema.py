"""Forward-only PostgreSQL schema and tenant-isolation contracts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class MigrationCursor(Protocol):
    """Minimal cursor surface required by the migration runner."""

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> MigrationCursor:
        """Execute one parameterized statement."""

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return rows from the last query."""


class MigrationConnection(Protocol):
    """Minimal transaction-aware connection surface for psycopg or a test double."""

    def cursor(self) -> MigrationCursor:
        """Return a cursor for the current transaction."""

    def transaction(self) -> AbstractContextManager[object]:
        """Return a transaction context manager."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable forward-only schema migration."""

    version: int
    name: str
    sql: str

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version <= 0:
            raise ValueError("migration version must be a positive integer")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("migration name must be non-empty")
        if not isinstance(self.sql, str) or not self.sql.strip():
            raise ValueError("migration SQL must be non-empty")


_TENANT_POLICY_TABLES = (
    "projects",
    "episodes",
    "command_receipts",
    "usage_events",
    "provider_evidence",
    "object_references",
    "publication_approvals",
    "publication_receipts",
    "outbox_events",
)

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "tenants",
        """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id UUID PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    Migration(
        2,
        "projects",
        """
        CREATE TABLE IF NOT EXISTS projects (
            tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
            project_id UUID NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, project_id)
        )
        """,
    ),
    Migration(
        3,
        "episodes",
        """
        CREATE TABLE IF NOT EXISTS episodes (
            tenant_id UUID NOT NULL,
            project_id UUID NOT NULL,
            episode_id UUID NOT NULL,
            idempotency_key TEXT NOT NULL,
            source_sha256 CHAR(64) NOT NULL,
            source_bytes BYTEA NOT NULL,
            profile_name TEXT NOT NULL,
            state TEXT NOT NULL,
            version INTEGER NOT NULL,
            package_sha256 CHAR(64),
            package_manifest_sha256 CHAR(64),
            failure JSONB,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, episode_id),
            UNIQUE (tenant_id, idempotency_key)
        )
        """,
    ),
    Migration(
        4,
        "command_receipts",
        """
        CREATE TABLE IF NOT EXISTS command_receipts (
            tenant_id UUID NOT NULL,
            project_id UUID NOT NULL,
            episode_id UUID NOT NULL,
            command TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            command_id UUID NOT NULL,
            state TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, command_id),
            UNIQUE (tenant_id, project_id, episode_id, command, idempotency_key)
        )
        """,
    ),
    Migration(
        5,
        "usage_events",
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            tenant_id UUID NOT NULL,
            project_id UUID NOT NULL,
            job_id UUID NOT NULL,
            provider_request_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            usage JSONB NOT NULL,
            currency CHAR(3) NOT NULL,
            estimated_cost NUMERIC(24, 9) NOT NULL,
            reconciled_cost NUMERIC(24, 9),
            occurred_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, provider_request_id)
        )
        """,
    ),
    Migration(
        6,
        "provider_evidence",
        """
        CREATE TABLE IF NOT EXISTS provider_evidence (
            tenant_id UUID NOT NULL,
            project_id UUID NOT NULL,
            job_id UUID NOT NULL,
            request_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            input_sha256 CHAR(64) NOT NULL,
            output_sha256 CHAR(64) NOT NULL,
            usage JSONB NOT NULL,
            currency CHAR(3) NOT NULL,
            estimated_cost NUMERIC(24, 9) NOT NULL,
            reconciled_cost NUMERIC(24, 9),
            latency_ms INTEGER NOT NULL,
            retry_count INTEGER NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            evidence_kind TEXT NOT NULL,
            PRIMARY KEY (tenant_id, request_id)
        )
        """,
    ),
    Migration(
        7,
        "object_references",
        """
        CREATE TABLE IF NOT EXISTS object_references (
            tenant_id UUID NOT NULL,
            project_id UUID NOT NULL,
            sha256 CHAR(64) NOT NULL,
            storage_key TEXT NOT NULL,
            media_type TEXT NOT NULL,
            byte_count BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, project_id, sha256)
        )
        """,
    ),
    Migration(
        8,
        "publication_approvals",
        """
        CREATE TABLE IF NOT EXISTS publication_approvals (
            tenant_id UUID NOT NULL,
            project_id UUID NOT NULL,
            approval_id UUID NOT NULL,
            target_id TEXT NOT NULL,
            package_sha256 CHAR(64) NOT NULL,
            actor_id TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, approval_id)
        )
        """,
    ),
    Migration(
        9,
        "publication_receipts",
        """
        CREATE TABLE IF NOT EXISTS publication_receipts (
            tenant_id UUID NOT NULL,
            project_id UUID NOT NULL,
            publication_id UUID NOT NULL,
            target_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            package_sha256 CHAR(64) NOT NULL,
            status TEXT NOT NULL,
            external_id TEXT,
            authorization JSONB NOT NULL,
            provenance JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, publication_id),
            UNIQUE (tenant_id, target_id, idempotency_key)
        )
        """,
    ),
    Migration(
        10,
        "outbox_events",
        """
        CREATE TABLE IF NOT EXISTS outbox_events (
            tenant_id UUID NOT NULL,
            event_id UUID NOT NULL,
            aggregate_type TEXT NOT NULL,
            aggregate_id UUID NOT NULL,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            published_at TIMESTAMPTZ,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, event_id)
        )
        """,
    ),
    Migration(
        11,
        "tenant_indexes",
        """
        CREATE INDEX IF NOT EXISTS usage_events_job_idx
            ON usage_events (tenant_id, job_id, occurred_at);
        CREATE INDEX IF NOT EXISTS outbox_pending_idx
            ON outbox_events (tenant_id, created_at)
            WHERE published_at IS NULL
        """,
    ),
    Migration(
        12,
        "enable_rls",
        ";\n".join(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"
            for table in _TENANT_POLICY_TABLES
        ),
    ),
    Migration(
        13,
        "tenant_rls_policies",
        ";\n".join(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('poddown.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('poddown.tenant_id', true)::uuid);"
            for table in _TENANT_POLICY_TABLES
        ),
    ),
    Migration(
        14,
        "episode_manifest_digest",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS "
        "package_manifest_sha256 CHAR(64)",
    ),
    Migration(
        15,
        "outbox_replay_index",
        "CREATE INDEX IF NOT EXISTS outbox_aggregate_idx ON outbox_events "
        "(tenant_id, aggregate_type, aggregate_id, created_at)",
    ),
    Migration(
        16,
        "outbox_project_scope",
        "ALTER TABLE outbox_events ADD COLUMN IF NOT EXISTS project_id UUID",
    ),
    Migration(
        17,
        "publication_approval_audit_fields",
        ";\n".join(
            (
                "ALTER TABLE publication_approvals ADD COLUMN IF NOT EXISTS "
                "episode_id UUID",
                "ALTER TABLE publication_approvals ADD COLUMN IF NOT EXISTS "
                "operation TEXT NOT NULL DEFAULT 'publish'",
                "ALTER TABLE publication_approvals ADD COLUMN IF NOT EXISTS "
                "nonce_sha256 CHAR(64)",
                "ALTER TABLE publication_approvals ADD COLUMN IF NOT EXISTS "
                "issued_at TIMESTAMPTZ",
            )
        ),
    ),
    Migration(
        18,
        "episode_record_fields",
        ";\n".join(
            (
                "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS "
                "request_fingerprint CHAR(64)",
                "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS source_content BYTEA",
                "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS "
                "source_byte_count BIGINT",
                "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS qa_evidence JSONB",
                "UPDATE episodes SET source_content = source_bytes "
                "WHERE source_content IS NULL",
                "UPDATE episodes SET source_byte_count = octet_length(source_content) "
                "WHERE source_byte_count IS NULL AND source_content IS NOT NULL",
                "UPDATE episodes SET request_fingerprint = source_sha256 "
                "WHERE request_fingerprint IS NULL",
            )
        ),
    ),
    Migration(
        19,
        "command_receipt_dispatch_fields",
        ";\n".join(
            (
                "ALTER TABLE command_receipts ADD COLUMN IF NOT EXISTS "
                "accepted BOOLEAN NOT NULL DEFAULT true",
                "ALTER TABLE command_receipts ADD COLUMN IF NOT EXISTS "
                "workflow_id TEXT",
                "ALTER TABLE command_receipts ADD COLUMN IF NOT EXISTS "
                "command_payload JSONB NOT NULL DEFAULT '{}'::jsonb",
            )
        ),
    ),
    Migration(
        20,
        "command_receipt_tenant_idempotency",
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "command_receipts_tenant_idempotency_idx ON command_receipts "
        "(tenant_id, idempotency_key)",
    ),
    Migration(
        21,
        "usage_units_contract",
        "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS units JSONB;\n"
        "UPDATE usage_events SET units = usage WHERE units IS NULL;\n"
        "ALTER TABLE usage_events ALTER COLUMN units SET NOT NULL",
    ),
    Migration(
        22,
        "command_receipt_updated_at",
        "ALTER TABLE command_receipts ADD COLUMN IF NOT EXISTS "
        "updated_at TIMESTAMPTZ;\n"
        "UPDATE command_receipts SET updated_at = created_at "
        "WHERE updated_at IS NULL;\n"
        "ALTER TABLE command_receipts ALTER COLUMN updated_at SET NOT NULL",
    ),
    Migration(
        23,
        "object_reference_name",
        "ALTER TABLE object_references ADD COLUMN IF NOT EXISTS name TEXT;\n"
        "UPDATE object_references SET name = 'legacy-' || "
        "substring(sha256 from 1 for 16) "
        "WHERE name IS NULL;\n"
        "ALTER TABLE object_references ALTER COLUMN name SET NOT NULL",
    ),
    Migration(
        24,
        "outbox_project_scope_backfill",
        "UPDATE outbox_events AS outbox SET project_id = episodes.project_id "
        "FROM episodes "
        "WHERE outbox.project_id IS NULL "
        "AND outbox.aggregate_type = 'episode' "
        "AND outbox.tenant_id = episodes.tenant_id "
        "AND outbox.aggregate_id = episodes.episode_id;\n"
        "ALTER TABLE outbox_events ALTER COLUMN project_id SET NOT NULL",
    ),
    Migration(
        25,
        "publication_receipt_audit_fields",
        ";\n".join(
            (
                "ALTER TABLE publication_receipts ADD COLUMN IF NOT EXISTS "
                "episode_version_id TEXT",
                "ALTER TABLE publication_receipts ADD COLUMN IF NOT EXISTS "
                "disclosure JSONB",
                "UPDATE publication_receipts SET episode_version_id = "
                "'legacy-' || publication_id::text "
                "WHERE episode_version_id IS NULL",
                "UPDATE publication_receipts SET disclosure = "
                '\'{"spoken": false, "show_notes": false, '
                '"platform": false}\'::jsonb '
                "WHERE disclosure IS NULL",
                "ALTER TABLE publication_receipts ALTER COLUMN episode_version_id "
                "SET NOT NULL",
                "ALTER TABLE publication_receipts ALTER COLUMN disclosure SET NOT NULL",
            )
        ),
    ),
    Migration(
        26,
        "object_inventory",
        """
        CREATE TABLE IF NOT EXISTS object_inventory (
            tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
            project_id UUID NOT NULL,
            storage_key TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, project_id, storage_key),
            FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects(tenant_id, project_id)
        )
        """,
    ),
    Migration(
        27,
        "object_inventory_rls",
        "ALTER TABLE object_inventory ENABLE ROW LEVEL SECURITY;\n"
        "CREATE POLICY object_inventory_tenant_isolation ON object_inventory "
        "USING (tenant_id = current_setting('poddown.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('poddown.tenant_id', true)::uuid)",
    ),
)


class MigrationRunner:
    """Apply schema migrations atomically and exactly once per version."""

    def __init__(self, migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
        if not isinstance(migrations, tuple) or not migrations:
            raise ValueError("migrations must be a non-empty tuple")
        if any(not isinstance(migration, Migration) for migration in migrations):
            raise TypeError("migrations must contain Migration values")
        versions = [migration.version for migration in migrations]
        if versions != sorted(set(versions)):
            raise ValueError("migration versions must be unique and ordered")
        self._migrations = migrations

    def apply(self, connection: MigrationConnection) -> tuple[int, ...]:
        """Apply unapplied migrations and return the versions applied this time."""
        with connection.transaction():
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS poddown_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                "SELECT version, name FROM poddown_schema_migrations ORDER BY version"
            )
            applied: dict[int, str] = {}
            for row in cursor.fetchall():
                if (
                    len(row) != 2
                    or type(row[0]) is not int
                    or not isinstance(row[1], str)
                ):
                    raise ValueError("schema migration records are malformed")
                applied[row[0]] = row[1]
            changed: list[int] = []
            for migration in self._migrations:
                previous_name = applied.get(migration.version)
                if previous_name is not None:
                    if previous_name != migration.name:
                        raise ValueError("applied migration name conflicts")
                    continue
                cursor.execute(migration.sql)
                cursor.execute(
                    "INSERT INTO poddown_schema_migrations (version, name) "
                    "VALUES (%s, %s)",
                    (migration.version, migration.name),
                )
                changed.append(migration.version)
            return tuple(changed)


def set_transaction_tenant(connection: MigrationConnection, tenant_id: UUID) -> None:
    """Set a transaction-local tenant setting required by every RLS query."""
    if not isinstance(tenant_id, UUID) or tenant_id.version != 7:
        raise ValueError("tenant_id must be a UUIDv7")
    connection.cursor().execute(
        "SELECT set_config('poddown.tenant_id', %s, true)",
        (str(tenant_id),),
    )


@contextmanager
def migration_transaction(connection: MigrationConnection) -> Iterator[object]:
    """Expose a small named context for callers composing schema operations."""
    with connection.transaction() as transaction:
        yield transaction


__all__ = [
    "MIGRATIONS",
    "Migration",
    "MigrationConnection",
    "MigrationCursor",
    "MigrationRunner",
    "migration_transaction",
    "set_transaction_tenant",
]
