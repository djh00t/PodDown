"""Forward-only PostgreSQL schema and tenant-isolation contracts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from poddown.migrations import Migration as PostgresCatalogMigration


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
            "authorization" JSONB NOT NULL,
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
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
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
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    ),
)


class MigrationRunner:
    """Apply schema migrations atomically and exactly once per version."""

    _POSTGRES_MIGRATION_LOCK = 860812401

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
                "SELECT pg_advisory_xact_lock(%s)",
                (self._POSTGRES_MIGRATION_LOCK,),
            )
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
        "SELECT set_config('app.tenant_id', %s, true)",
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

# ruff: noqa: E501

_TENANCY_STATEMENTS = (
    "CREATE TABLE tenants ("
    "id UUID CONSTRAINT pk_tenants PRIMARY KEY, "
    "slug TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_tenants_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT uq_tenants_slug UNIQUE (slug)"
    ")",
    "CREATE TABLE projects ("
    "id UUID CONSTRAINT pk_projects PRIMARY KEY, "
    "tenant_id UUID NOT NULL, "
    "slug TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_projects_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT fk_projects_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_projects_tenant_slug UNIQUE (tenant_id, slug)"
    ")",
    "CREATE INDEX ix_projects_tenant_id ON projects (tenant_id)",
)

_EPISODE_STATEMENTS = (
    "ALTER TABLE projects ADD CONSTRAINT uq_projects_tenant_id UNIQUE (tenant_id, id)",
    "CREATE TABLE source_documents ("
    "id UUID CONSTRAINT pk_source_documents PRIMARY KEY, "
    "tenant_id UUID NOT NULL, "
    "sha256 CHAR(64) NOT NULL, "
    "source_reference TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_source_documents_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_source_documents_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT fk_source_documents_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_source_documents_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_source_documents_tenant_id_sha256 "
    "UNIQUE (tenant_id, id, sha256), "
    "CONSTRAINT uq_source_documents_tenant_sha256 UNIQUE (tenant_id, sha256)"
    ")",
    "CREATE TABLE episodes ("
    "id UUID CONSTRAINT pk_episodes PRIMARY KEY, "
    "tenant_id UUID NOT NULL, "
    "project_id UUID NOT NULL, "
    "idempotency_key TEXT NOT NULL, "
    "version INTEGER NOT NULL DEFAULT 1, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_episodes_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_episodes_version CHECK (version > 0), "
    "CONSTRAINT fk_episodes_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_episodes_tenant_project FOREIGN KEY (tenant_id, project_id) "
    "REFERENCES projects (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_episodes_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_episodes_tenant_idempotency_key "
    "UNIQUE (tenant_id, idempotency_key)"
    ")",
    "CREATE TABLE episode_versions ("
    "id UUID CONSTRAINT pk_episode_versions PRIMARY KEY, "
    "tenant_id UUID NOT NULL, "
    "episode_id UUID NOT NULL, "
    "source_document_id UUID NOT NULL, "
    "version INTEGER NOT NULL, "
    "source_sha256 CHAR(64) NOT NULL, "
    "script_sha256 CHAR(64) NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_episode_versions_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_episode_versions_version CHECK (version > 0), "
    "CONSTRAINT ck_episode_versions_source_sha256 "
    "CHECK (source_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_episode_versions_script_sha256 "
    "CHECK (script_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT fk_episode_versions_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_episode_versions_tenant_episode "
    "FOREIGN KEY (tenant_id, episode_id) REFERENCES episodes (tenant_id, id) "
    "ON DELETE RESTRICT, "
    "CONSTRAINT fk_episode_versions_tenant_source_document "
    "FOREIGN KEY (tenant_id, source_document_id) "
    "REFERENCES source_documents (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_episode_versions_tenant_source_document_hash "
    "FOREIGN KEY (tenant_id, source_document_id, source_sha256) "
    "REFERENCES source_documents (tenant_id, id, sha256) ON DELETE RESTRICT, "
    "CONSTRAINT uq_episode_versions_episode_version UNIQUE (episode_id, version)"
    ")",
    "CREATE FUNCTION reject_source_documents_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'source_documents are immutable'; END; $$",
    "CREATE TRIGGER trg_source_documents_immutable "
    "BEFORE UPDATE OR DELETE ON source_documents FOR EACH ROW "
    "EXECUTE FUNCTION reject_source_documents_mutation()",
    "CREATE FUNCTION reject_episode_versions_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'episode_versions are immutable'; END; $$",
    "CREATE TRIGGER trg_episode_versions_immutable "
    "BEFORE UPDATE OR DELETE ON episode_versions FOR EACH ROW "
    "EXECUTE FUNCTION reject_episode_versions_mutation()",
    "CREATE INDEX ix_source_documents_tenant_id ON source_documents (tenant_id)",
    "CREATE INDEX ix_episodes_tenant_project_id ON episodes (tenant_id, project_id)",
    "CREATE INDEX ix_episode_versions_tenant_episode_id "
    "ON episode_versions (tenant_id, episode_id)",
)

_CONTENT_STATEMENTS = (
    "CREATE TABLE scripts ("
    "id UUID CONSTRAINT pk_scripts PRIMARY KEY, "
    "tenant_id UUID NOT NULL, "
    "sha256 CHAR(64) NOT NULL, "
    "content JSONB NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_scripts_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_scripts_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT fk_scripts_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_scripts_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_scripts_tenant_id_sha256 UNIQUE (tenant_id, id, sha256), "
    "CONSTRAINT uq_scripts_tenant_sha256 UNIQUE (tenant_id, sha256)"
    ")",
    "ALTER TABLE episode_versions ADD CONSTRAINT "
    "fk_episode_versions_tenant_script_hash "
    "FOREIGN KEY (tenant_id, script_sha256) "
    "REFERENCES scripts (tenant_id, sha256) ON DELETE RESTRICT",
    "CREATE TABLE script_versions ("
    "id UUID CONSTRAINT pk_script_versions PRIMARY KEY, "
    "tenant_id UUID NOT NULL, "
    "script_id UUID NOT NULL, "
    "version INTEGER NOT NULL, "
    "script_sha256 CHAR(64) NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_script_versions_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_script_versions_version CHECK (version > 0), "
    "CONSTRAINT ck_script_versions_sha256 "
    "CHECK (script_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT fk_script_versions_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_script_versions_tenant_script "
    "FOREIGN KEY (tenant_id, script_id) REFERENCES scripts (tenant_id, id) "
    "ON DELETE RESTRICT, "
    "CONSTRAINT fk_script_versions_tenant_script_hash "
    "FOREIGN KEY (tenant_id, script_id, script_sha256) "
    "REFERENCES scripts (tenant_id, id, sha256) ON DELETE RESTRICT, "
    "CONSTRAINT uq_script_versions_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_script_versions_script_version UNIQUE (script_id, version)"
    ")",
    "CREATE TABLE profiles ("
    "id UUID CONSTRAINT pk_profiles PRIMARY KEY, "
    "tenant_id UUID NOT NULL, "
    "profile_id TEXT NOT NULL, "
    "version TEXT NOT NULL, "
    "content JSONB NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_profiles_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT fk_profiles_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_profiles_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_profiles_tenant_profile_version "
    "UNIQUE (tenant_id, profile_id, version)"
    ")",
    "CREATE TABLE speakers ("
    "id UUID CONSTRAINT pk_speakers PRIMARY KEY, "
    "tenant_id UUID NOT NULL, "
    "profile_id UUID NOT NULL, "
    "speaker_id TEXT NOT NULL, "
    "display_name TEXT NOT NULL, "
    "voice_asset_id TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_speakers_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT fk_speakers_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_speakers_tenant_profile "
    "FOREIGN KEY (tenant_id, profile_id) REFERENCES profiles (tenant_id, id) "
    "ON DELETE RESTRICT, "
    "CONSTRAINT uq_speakers_profile_speaker_id UNIQUE (profile_id, speaker_id)"
    ")",
    "CREATE FUNCTION reject_scripts_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'scripts are immutable'; END; $$",
    "CREATE TRIGGER trg_scripts_immutable "
    "BEFORE UPDATE OR DELETE ON scripts FOR EACH ROW "
    "EXECUTE FUNCTION reject_scripts_mutation()",
    "CREATE FUNCTION reject_script_versions_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'script_versions are immutable'; END; $$",
    "CREATE TRIGGER trg_script_versions_immutable "
    "BEFORE UPDATE OR DELETE ON script_versions FOR EACH ROW "
    "EXECUTE FUNCTION reject_script_versions_mutation()",
    "CREATE INDEX ix_script_versions_tenant_script_id "
    "ON script_versions (tenant_id, script_id)",
    "CREATE INDEX ix_profiles_tenant_id ON profiles (tenant_id)",
    "CREATE INDEX ix_speakers_tenant_profile_id ON speakers (tenant_id, profile_id)",
)


_VOICE_PRONUNCIATION_STATEMENTS = (
    "CREATE TABLE voice_assets ("
    "id UUID CONSTRAINT pk_voice_assets PRIMARY KEY, tenant_id UUID NOT NULL, "
    "asset_id TEXT NOT NULL, provider TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_voice_assets_id_uuidv7 CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_voice_assets_provider CHECK (provider IN ('local', 'elevenlabs', 'openai')), "
    "CONSTRAINT fk_voice_assets_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_voice_assets_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_voice_assets_tenant_asset_id UNIQUE (tenant_id, asset_id)"
    ")",
    "CREATE TABLE voice_profiles ("
    "id UUID CONSTRAINT pk_voice_profiles PRIMARY KEY, tenant_id UUID NOT NULL, "
    "voice_asset_id UUID NOT NULL, profile_id TEXT NOT NULL, version TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_voice_profiles_id_uuidv7 CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT fk_voice_profiles_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_voice_profiles_tenant_voice_asset FOREIGN KEY (tenant_id, voice_asset_id) "
    "REFERENCES voice_assets (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_voice_profiles_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_voice_profiles_tenant_profile_version UNIQUE (tenant_id, profile_id, version)"
    ")",
    "ALTER TABLE speakers ADD COLUMN voice_profile_id UUID",
    "ALTER TABLE speakers ADD CONSTRAINT fk_speakers_tenant_voice_profile "
    "FOREIGN KEY (tenant_id, voice_profile_id) REFERENCES voice_profiles (tenant_id, id) ON DELETE RESTRICT",
    "CREATE TABLE voice_consents ("
    "id UUID CONSTRAINT pk_voice_consents PRIMARY KEY, tenant_id UUID NOT NULL, "
    "voice_asset_id UUID NOT NULL, allowed_providers TEXT[] NOT NULL, evidence_reference TEXT NOT NULL, "
    "valid_from TIMESTAMPTZ NOT NULL, valid_until TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_voice_consents_id_uuidv7 CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_voice_consents_allowed_providers CHECK (cardinality(allowed_providers) > 0 "
    "AND allowed_providers <@ ARRAY['local', 'elevenlabs', 'openai']::TEXT[]), "
    "CONSTRAINT ck_voice_consents_evidence_reference CHECK (btrim(evidence_reference) <> ''), "
    "CONSTRAINT ck_voice_consents_validity_window CHECK (valid_until > valid_from), "
    "CONSTRAINT ck_voice_consents_revocation CHECK (revoked_at IS NULL OR revoked_at >= valid_from), "
    "CONSTRAINT fk_voice_consents_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_voice_consents_tenant_voice_asset FOREIGN KEY (tenant_id, voice_asset_id) "
    "REFERENCES voice_assets (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_voice_consents_tenant_id UNIQUE (tenant_id, id)"
    ")",
    "CREATE TABLE pronunciation_lexicons ("
    "id UUID CONSTRAINT pk_pronunciation_lexicons PRIMARY KEY, tenant_id UUID NOT NULL, "
    "scope TEXT NOT NULL, scope_reference TEXT NOT NULL, version TEXT NOT NULL, precedence SMALLINT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_pronunciation_lexicons_id_uuidv7 CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_pronunciation_lexicons_scope_precedence CHECK ((scope = 'episode' AND precedence = 1) "
    "OR (scope = 'project' AND precedence = 2) OR (scope = 'domain' AND precedence = 3) "
    "OR (scope = 'global' AND precedence = 4)), "
    "CONSTRAINT fk_pronunciation_lexicons_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_pronunciation_lexicons_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_pronunciation_lexicons_tenant_scope_version UNIQUE (tenant_id, scope, scope_reference, version)"
    ")",
    "CREATE TABLE pronunciation_entries ("
    "id UUID CONSTRAINT pk_pronunciation_entries PRIMARY KEY, tenant_id UUID NOT NULL, lexicon_id UUID NOT NULL, "
    "normalized_key TEXT NOT NULL, spoken_form TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'technical_term', "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_pronunciation_entries_id_uuidv7 CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_pronunciation_entries_normalized_key CHECK (normalized_key = lower(btrim(normalized_key)) "
    "AND normalized_key <> ''), CONSTRAINT ck_pronunciation_entries_spoken_form CHECK (btrim(spoken_form) <> ''), "
    "CONSTRAINT ck_pronunciation_entries_category CHECK (category IN ('name', 'organization', 'product', 'technical_term')), "
    "CONSTRAINT fk_pronunciation_entries_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_pronunciation_entries_tenant_lexicon FOREIGN KEY (tenant_id, lexicon_id) "
    "REFERENCES pronunciation_lexicons (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_pronunciation_entries_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_pronunciation_entries_lexicon_normalized_key UNIQUE (lexicon_id, normalized_key)"
    ")",
    "CREATE INDEX ix_voice_assets_tenant_id ON voice_assets (tenant_id)",
    "CREATE INDEX ix_voice_profiles_tenant_voice_asset_id ON voice_profiles (tenant_id, voice_asset_id)",
    "CREATE INDEX ix_voice_consents_tenant_voice_asset_id ON voice_consents (tenant_id, voice_asset_id)",
    "CREATE INDEX ix_pronunciation_lexicons_tenant_scope_precedence "
    "ON pronunciation_lexicons (tenant_id, scope, scope_reference, precedence)",
    "CREATE INDEX ix_pronunciation_entries_tenant_lexicon_id ON pronunciation_entries (tenant_id, lexicon_id)",
)


_RENDER_STATEMENTS = (
    "ALTER TABLE episodes ADD CONSTRAINT uq_episodes_tenant_id_project_id "
    "UNIQUE (tenant_id, id, project_id)",
    "ALTER TABLE episode_versions ADD CONSTRAINT uq_episode_versions_tenant_id "
    "UNIQUE (tenant_id, id)",
    "ALTER TABLE episode_versions ADD CONSTRAINT "
    "uq_episode_versions_tenant_id_script_sha256 "
    "UNIQUE (tenant_id, id, script_sha256)",
    "ALTER TABLE episode_versions ADD CONSTRAINT "
    "uq_episode_versions_tenant_id_episode_id_script_sha256 "
    "UNIQUE (tenant_id, id, episode_id, script_sha256)",
    "CREATE TABLE render_jobs ("
    "id UUID CONSTRAINT pk_render_jobs PRIMARY KEY, "
    "tenant_id UUID NOT NULL, project_id UUID NOT NULL, episode_id UUID NOT NULL, "
    "episode_version_id UUID NOT NULL, workflow_id TEXT NOT NULL, "
    "content_sha256 CHAR(64) NOT NULL, request_sha256 CHAR(64) NOT NULL, "
    "status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_render_jobs_id_uuidv7 CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_render_jobs_content_sha256 CHECK (content_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_render_jobs_request_sha256 CHECK (request_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_render_jobs_status CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')), "
    "CONSTRAINT ck_render_jobs_attempt CHECK (attempt > 0), "
    "CONSTRAINT fk_render_jobs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_render_jobs_tenant_project FOREIGN KEY (tenant_id, project_id) "
    "REFERENCES projects (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_render_jobs_tenant_episode FOREIGN KEY (tenant_id, episode_id) "
    "REFERENCES episodes (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_render_jobs_tenant_episode_project "
    "FOREIGN KEY (tenant_id, episode_id, project_id) "
    "REFERENCES episodes (tenant_id, id, project_id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_render_jobs_tenant_episode_version_content "
    "FOREIGN KEY (tenant_id, episode_version_id, episode_id, content_sha256) "
    "REFERENCES episode_versions (tenant_id, id, episode_id, script_sha256) "
    "ON DELETE RESTRICT, "
    "CONSTRAINT uq_render_jobs_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_render_jobs_tenant_workflow_id UNIQUE (tenant_id, workflow_id), "
    "CONSTRAINT uq_render_jobs_tenant_id_request_sha256 "
    "UNIQUE (tenant_id, id, request_sha256)"
    ")",
    "CREATE TABLE render_segments ("
    "id UUID CONSTRAINT pk_render_segments PRIMARY KEY, "
    "tenant_id UUID NOT NULL, render_job_id UUID NOT NULL, segment_id TEXT NOT NULL, "
    "attempt INTEGER NOT NULL, status TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_render_segments_id_uuidv7 CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_render_segments_attempt CHECK (attempt > 0), "
    "CONSTRAINT ck_render_segments_status CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')), "
    "CONSTRAINT fk_render_segments_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_render_segments_tenant_job FOREIGN KEY (tenant_id, render_job_id) "
    "REFERENCES render_jobs (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_render_segments_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_render_segments_tenant_id_job UNIQUE "
    "(tenant_id, id, render_job_id), "
    "CONSTRAINT uq_render_segments_job_segment_attempt "
    "UNIQUE (render_job_id, segment_id, attempt)"
    ")",
    "CREATE TABLE render_candidates ("
    "id UUID CONSTRAINT pk_render_candidates PRIMARY KEY, "
    "tenant_id UUID NOT NULL, render_job_id UUID NOT NULL, render_segment_id UUID NOT NULL, "
    "candidate_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, take_index INTEGER NOT NULL, "
    "request_sha256 CHAR(64) NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, "
    "provider_request_id TEXT NOT NULL, provider_metadata JSONB NOT NULL, "
    "cost_reference UUID NOT NULL, audio_sha256 CHAR(64) NOT NULL, response_sha256 CHAR(64) NOT NULL, "
    "status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_render_candidates_id_uuidv7 CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_render_candidates_take_index CHECK (take_index >= 0), "
    "CONSTRAINT ck_render_candidates_request_sha256 CHECK (request_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_render_candidates_audio_sha256 CHECK (audio_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_render_candidates_response_sha256 CHECK (response_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_render_candidates_status CHECK (status IN ('pending', 'accepted', 'rejected', 'failed')), "
    "CONSTRAINT fk_render_candidates_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_render_candidates_tenant_job_request "
    "FOREIGN KEY (tenant_id, render_job_id, request_sha256) "
    "REFERENCES render_jobs (tenant_id, id, request_sha256) ON DELETE RESTRICT, "
    "CONSTRAINT fk_render_candidates_tenant_segment_job "
    "FOREIGN KEY (tenant_id, render_segment_id, render_job_id) "
    "REFERENCES render_segments (tenant_id, id, render_job_id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_render_candidates_segment_take "
    "UNIQUE (render_segment_id, take_index), "
    "CONSTRAINT uq_render_candidates_tenant_idempotency_key "
    "UNIQUE (tenant_id, idempotency_key), "
    "CONSTRAINT uq_render_candidates_tenant_provider_request "
    "UNIQUE (tenant_id, provider, provider_request_id)"
    ")",
    "CREATE FUNCTION reject_render_jobs_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF TG_OP = 'DELETE' OR "
    "(OLD.tenant_id, OLD.project_id, OLD.episode_id, OLD.episode_version_id, "
    "OLD.workflow_id, OLD.content_sha256, OLD.request_sha256) IS DISTINCT FROM "
    "(NEW.tenant_id, NEW.project_id, NEW.episode_id, NEW.episode_version_id, "
    "NEW.workflow_id, NEW.content_sha256, NEW.request_sha256) THEN "
    "RAISE EXCEPTION 'render_jobs provenance is immutable'; END IF; "
    "RETURN NEW; END; $$",
    "CREATE TRIGGER trg_render_jobs_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON render_jobs FOR EACH ROW "
    "EXECUTE FUNCTION reject_render_jobs_provenance_mutation()",
    "CREATE FUNCTION reject_render_segments_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF TG_OP = 'DELETE' OR "
    "(OLD.tenant_id, OLD.render_job_id, OLD.segment_id, OLD.attempt) IS DISTINCT FROM "
    "(NEW.tenant_id, NEW.render_job_id, NEW.segment_id, NEW.attempt) THEN "
    "RAISE EXCEPTION 'render_segments provenance is immutable'; END IF; "
    "RETURN NEW; END; $$",
    "CREATE TRIGGER trg_render_segments_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON render_segments FOR EACH ROW "
    "EXECUTE FUNCTION reject_render_segments_provenance_mutation()",
    "CREATE FUNCTION reject_render_candidates_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF TG_OP = 'DELETE' OR "
    "(OLD.tenant_id, OLD.render_job_id, OLD.render_segment_id, OLD.candidate_id, "
    "OLD.idempotency_key, OLD.take_index, OLD.request_sha256, OLD.provider, "
    "OLD.model, OLD.provider_request_id, OLD.provider_metadata, OLD.cost_reference, "
    "OLD.audio_sha256, OLD.response_sha256, OLD.created_at) IS DISTINCT FROM "
    "(NEW.tenant_id, NEW.render_job_id, NEW.render_segment_id, NEW.candidate_id, "
    "NEW.idempotency_key, NEW.take_index, NEW.request_sha256, NEW.provider, "
    "NEW.model, NEW.provider_request_id, NEW.provider_metadata, NEW.cost_reference, "
    "NEW.audio_sha256, NEW.response_sha256, NEW.created_at) THEN "
    "RAISE EXCEPTION 'render_candidates provenance is immutable'; END IF; "
    "RETURN NEW; END; $$",
    "CREATE TRIGGER trg_render_candidates_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON render_candidates FOR EACH ROW "
    "EXECUTE FUNCTION reject_render_candidates_provenance_mutation()",
    "CREATE INDEX ix_render_jobs_tenant_project_episode_version_id "
    "ON render_jobs (tenant_id, project_id, episode_version_id)",
    "CREATE INDEX ix_render_segments_tenant_job_id "
    "ON render_segments (tenant_id, render_job_id)",
    "CREATE INDEX ix_render_candidates_tenant_segment_id "
    "ON render_candidates (tenant_id, render_segment_id)",
)

_AUDIO_QA_STATEMENTS = (
    "ALTER TABLE render_candidates ADD CONSTRAINT "
    "uq_render_candidates_tenant_id_response_audio "
    "UNIQUE (tenant_id, id, response_sha256, audio_sha256)",
    "ALTER TABLE episode_versions ADD CONSTRAINT "
    "uq_episode_versions_tenant_id_source_sha256 "
    "UNIQUE (tenant_id, id, source_sha256)",
    "CREATE TABLE transcripts ("
    "id UUID CONSTRAINT pk_transcripts PRIMARY KEY, "
    "tenant_id UUID NOT NULL, render_candidate_id UUID NOT NULL, "
    "render_response_sha256 CHAR(64) NOT NULL, audio_sha256 CHAR(64) NOT NULL, "
    "transcript_sha256 CHAR(64) NOT NULL, evidence_kind TEXT NOT NULL, "
    "provider TEXT, provider_request_id TEXT, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_transcripts_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_transcripts_render_response_sha256 "
    "CHECK (render_response_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_transcripts_audio_sha256 CHECK (audio_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_transcripts_transcript_sha256 "
    "CHECK (transcript_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_transcripts_evidence_kind "
    "CHECK (evidence_kind IN ('provider-asr', 'script-derived')), "
    "CONSTRAINT ck_transcripts_provider_evidence CHECK ("
    "(evidence_kind = 'provider-asr' AND provider IS NOT NULL "
    "AND provider_request_id IS NOT NULL) OR "
    "(evidence_kind = 'script-derived' AND provider IS NULL "
    "AND provider_request_id IS NULL)), "
    "CONSTRAINT fk_transcripts_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_transcripts_tenant_candidate_provenance "
    "FOREIGN KEY (tenant_id, render_candidate_id, render_response_sha256, audio_sha256) "
    "REFERENCES render_candidates (tenant_id, id, response_sha256, audio_sha256) "
    "ON DELETE RESTRICT, "
    "CONSTRAINT uq_transcripts_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_transcripts_tenant_id_audio UNIQUE (tenant_id, id, audio_sha256), "
    "CONSTRAINT uq_transcripts_candidate_evidence "
    "UNIQUE (render_candidate_id, transcript_sha256, evidence_kind)"
    ")",
    "CREATE TABLE qa_results ("
    "id UUID CONSTRAINT pk_qa_results PRIMARY KEY, "
    "tenant_id UUID NOT NULL, transcript_id UUID NOT NULL, audio_sha256 CHAR(64) NOT NULL, "
    "gate_name TEXT NOT NULL, gate_status TEXT NOT NULL, gate_evidence JSONB NOT NULL, "
    "gate_evidence_sha256 CHAR(64) NOT NULL, "
    "critical_token_accuracy NUMERIC(5,4) NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_qa_results_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_qa_results_audio_sha256 CHECK (audio_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_qa_results_gate_evidence_sha256 "
    "CHECK (gate_evidence_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_qa_results_gate_status CHECK (gate_status IN ('pass', 'fail')), "
    "CONSTRAINT ck_qa_results_critical_token_accuracy "
    "CHECK (critical_token_accuracy >= 0.0000 AND critical_token_accuracy <= 1.0000), "
    "CONSTRAINT ck_qa_results_pass_requires_perfect_critical_tokens "
    "CHECK (gate_status <> 'pass' OR critical_token_accuracy = 1.0000), "
    "CONSTRAINT fk_qa_results_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_qa_results_tenant_transcript_audio "
    "FOREIGN KEY (tenant_id, transcript_id, audio_sha256) "
    "REFERENCES transcripts (tenant_id, id, audio_sha256) ON DELETE RESTRICT, "
    "CONSTRAINT uq_qa_results_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_qa_results_transcript_gate UNIQUE (transcript_id, gate_name)"
    ")",
    "CREATE TABLE audio_masters ("
    "id UUID CONSTRAINT pk_audio_masters PRIMARY KEY, "
    "tenant_id UUID NOT NULL, episode_version_id UUID NOT NULL, source_sha256 CHAR(64) NOT NULL, "
    "audio_sha256 CHAR(64) NOT NULL, mastering_profile_id TEXT NOT NULL, "
    "mastering_profile_version TEXT NOT NULL, mastering_metadata JSONB NOT NULL, "
    "mastering_metadata_sha256 CHAR(64) NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_audio_masters_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_audio_masters_source_sha256 "
    "CHECK (source_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_audio_masters_audio_sha256 CHECK (audio_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_audio_masters_metadata_sha256 "
    "CHECK (mastering_metadata_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT fk_audio_masters_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_audio_masters_tenant_episode_version_source "
    "FOREIGN KEY (tenant_id, episode_version_id, source_sha256) "
    "REFERENCES episode_versions (tenant_id, id, source_sha256) ON DELETE RESTRICT, "
    "CONSTRAINT uq_audio_masters_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_audio_masters_episode_version_profile "
    "UNIQUE (episode_version_id, mastering_profile_id, mastering_profile_version), "
    "CONSTRAINT uq_audio_masters_audio_sha256 UNIQUE (tenant_id, audio_sha256)"
    ")",
    "CREATE FUNCTION reject_transcripts_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'transcripts are immutable evidence'; END; $$",
    "CREATE TRIGGER trg_transcripts_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON transcripts FOR EACH ROW "
    "EXECUTE FUNCTION reject_transcripts_provenance_mutation()",
    "CREATE FUNCTION reject_qa_results_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'qa_results are immutable evidence'; END; $$",
    "CREATE TRIGGER trg_qa_results_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON qa_results FOR EACH ROW "
    "EXECUTE FUNCTION reject_qa_results_provenance_mutation()",
    "CREATE FUNCTION reject_audio_masters_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'audio_masters are immutable evidence'; END; $$",
    "CREATE TRIGGER trg_audio_masters_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON audio_masters FOR EACH ROW "
    "EXECUTE FUNCTION reject_audio_masters_provenance_mutation()",
    "CREATE INDEX ix_transcripts_tenant_candidate_id "
    "ON transcripts (tenant_id, render_candidate_id)",
    "CREATE INDEX ix_qa_results_tenant_transcript_id "
    "ON qa_results (tenant_id, transcript_id)",
    "CREATE INDEX ix_audio_masters_tenant_episode_version_id "
    "ON audio_masters (tenant_id, episode_version_id)",
)

_AUDIO_MASTER_METADATA_DIGEST_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    "ALTER TABLE audio_masters ADD CONSTRAINT "
    "ck_audio_masters_metadata_sha256_matches_metadata "
    "CHECK (mastering_metadata_sha256 = "
    "encode(digest(mastering_metadata::text, 'sha256'), 'hex'))",
)

_PUBLISHING_METERING_STATEMENTS = (
    "CREATE TABLE publishing_targets ("
    "id UUID CONSTRAINT pk_publishing_targets PRIMARY KEY, "
    "tenant_id UUID NOT NULL, provider TEXT NOT NULL, target_reference TEXT NOT NULL, "
    "display_name TEXT NOT NULL, disclosure_policy JSONB NOT NULL DEFAULT '{}'::jsonb, "
    "revoked_at TIMESTAMPTZ, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_publishing_targets_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_publishing_targets_provider CHECK (btrim(provider) <> ''), "
    "CONSTRAINT ck_publishing_targets_target_reference "
    "CHECK (btrim(target_reference) <> ''), "
    "CONSTRAINT ck_publishing_targets_display_name CHECK (btrim(display_name) <> ''), "
    "CONSTRAINT fk_publishing_targets_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_publishing_targets_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_publishing_targets_tenant_provider_target "
    "UNIQUE (tenant_id, provider, target_reference)"
    ")",
    "CREATE TABLE publications ("
    "id UUID CONSTRAINT pk_publications PRIMARY KEY, "
    "tenant_id UUID NOT NULL, project_id UUID NOT NULL, episode_id UUID NOT NULL, "
    "audio_master_id UUID NOT NULL, publishing_target_id UUID NOT NULL, "
    "idempotency_key TEXT NOT NULL, package_sha256 CHAR(64) NOT NULL, "
    "provider TEXT NOT NULL, provider_request_id TEXT NOT NULL, status TEXT NOT NULL, "
    "publication_receipt JSONB, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_publications_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_publications_idempotency_key CHECK (btrim(idempotency_key) <> ''), "
    "CONSTRAINT ck_publications_package_sha256 "
    "CHECK (package_sha256 ~ '^[0-9a-f]{64}$'), "
    "CONSTRAINT ck_publications_provider CHECK (btrim(provider) <> ''), "
    "CONSTRAINT ck_publications_provider_request_id "
    "CHECK (btrim(provider_request_id) <> ''), "
    "CONSTRAINT ck_publications_status "
    "CHECK (status IN ('pending', 'published', 'failed', 'cancelled')), "
    "CONSTRAINT fk_publications_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_publications_tenant_project FOREIGN KEY (tenant_id, project_id) "
    "REFERENCES projects (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_publications_tenant_episode_project "
    "FOREIGN KEY (tenant_id, episode_id, project_id) "
    "REFERENCES episodes (tenant_id, id, project_id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_publications_tenant_audio_master "
    "FOREIGN KEY (tenant_id, audio_master_id) "
    "REFERENCES audio_masters (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_publications_tenant_target "
    "FOREIGN KEY (tenant_id, publishing_target_id) "
    "REFERENCES publishing_targets (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_publications_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_publications_tenant_idempotency_key "
    "UNIQUE (tenant_id, idempotency_key), "
    "CONSTRAINT uq_publications_tenant_provider_request "
    "UNIQUE (tenant_id, provider, provider_request_id)"
    ")",
    "CREATE TABLE publication_approvals ("
    "id UUID CONSTRAINT pk_publication_approvals PRIMARY KEY, "
    "tenant_id UUID NOT NULL, publication_id UUID NOT NULL, operation TEXT NOT NULL, "
    "approval_reference TEXT NOT NULL, approved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_publication_approvals_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_publication_approvals_operation "
    "CHECK (operation IN ('publish', 'update', 'delete')), "
    "CONSTRAINT ck_publication_approvals_reference CHECK (btrim(approval_reference) <> ''), "
    "CONSTRAINT ck_publication_approvals_expiry "
    "CHECK (expires_at > approved_at), "
    "CONSTRAINT fk_publication_approvals_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_publication_approvals_tenant_publication "
    "FOREIGN KEY (tenant_id, publication_id) "
    "REFERENCES publications (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_publication_approvals_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_publication_approvals_tenant_reference "
    "UNIQUE (tenant_id, approval_reference)"
    ")",
    "CREATE TABLE usage_events ("
    "id UUID CONSTRAINT pk_usage_events PRIMARY KEY, "
    "tenant_id UUID NOT NULL, publication_id UUID NOT NULL, provider TEXT NOT NULL, "
    "provider_request_id TEXT NOT NULL, operation TEXT NOT NULL, unit_type TEXT NOT NULL, "
    "units NUMERIC(20,6) NOT NULL, currency CHAR(3) NOT NULL, "
    "estimated_cost NUMERIC(20,6) NOT NULL, reconciled_cost NUMERIC(20,6), "
    "occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_usage_events_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_usage_events_provider CHECK (btrim(provider) <> ''), "
    "CONSTRAINT ck_usage_events_provider_request_id CHECK (btrim(provider_request_id) <> ''), "
    "CONSTRAINT ck_usage_events_operation CHECK (btrim(operation) <> ''), "
    "CONSTRAINT ck_usage_events_unit_type CHECK (btrim(unit_type) <> ''), "
    "CONSTRAINT ck_usage_events_units CHECK (units >= 0), "
    "CONSTRAINT ck_usage_events_costs "
    "CHECK (estimated_cost >= 0 AND (reconciled_cost IS NULL OR reconciled_cost >= 0)), "
    "CONSTRAINT ck_usage_events_currency CHECK (currency ~ '^[A-Z]{3}$'), "
    "CONSTRAINT fk_usage_events_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_usage_events_tenant_publication "
    "FOREIGN KEY (tenant_id, publication_id) "
    "REFERENCES publications (tenant_id, id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_usage_events_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_usage_events_tenant_provider_request "
    "UNIQUE (tenant_id, provider, provider_request_id), "
    "CONSTRAINT uq_usage_events_tenant_id_provider_request "
    "UNIQUE (tenant_id, id, provider, provider_request_id)"
    ")",
    "CREATE TABLE provider_costs ("
    "id UUID CONSTRAINT pk_provider_costs PRIMARY KEY, "
    "tenant_id UUID NOT NULL, usage_event_id UUID NOT NULL, provider TEXT NOT NULL, "
    "provider_request_id TEXT NOT NULL, cost_kind TEXT NOT NULL, currency CHAR(3) NOT NULL, "
    "amount NUMERIC(20,6) NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_provider_costs_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_provider_costs_provider CHECK (btrim(provider) <> ''), "
    "CONSTRAINT ck_provider_costs_provider_request_id CHECK (btrim(provider_request_id) <> ''), "
    "CONSTRAINT ck_provider_costs_kind CHECK (cost_kind IN ('estimated', 'reconciled')), "
    "CONSTRAINT ck_provider_costs_currency CHECK (currency ~ '^[A-Z]{3}$'), "
    "CONSTRAINT ck_provider_costs_amount CHECK (amount >= 0), "
    "CONSTRAINT fk_provider_costs_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT fk_provider_costs_tenant_usage_event "
    "FOREIGN KEY (tenant_id, usage_event_id, provider, provider_request_id) "
    "REFERENCES usage_events (tenant_id, id, provider, provider_request_id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_provider_costs_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_provider_costs_tenant_provider_request "
    "UNIQUE (tenant_id, provider, provider_request_id, cost_kind)"
    ")",
    "ALTER TABLE render_candidates ADD CONSTRAINT "
    "fk_render_candidates_tenant_cost_reference "
    "FOREIGN KEY (tenant_id, cost_reference) "
    "REFERENCES provider_costs (tenant_id, id) ON DELETE RESTRICT",
    "CREATE TABLE outbox_events ("
    "id UUID CONSTRAINT pk_outbox_events PRIMARY KEY, "
    "tenant_id UUID NOT NULL, aggregate_type TEXT NOT NULL, aggregate_id UUID NOT NULL, "
    "aggregate_version BIGINT NOT NULL, event_type TEXT NOT NULL, payload JSONB NOT NULL, "
    "publish_attempts INTEGER NOT NULL DEFAULT 0, published_at TIMESTAMPTZ, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT ck_outbox_events_id_uuidv7 "
    "CHECK (substring(id::text FROM 15 FOR 1) = '7'), "
    "CONSTRAINT ck_outbox_events_aggregate_type CHECK (btrim(aggregate_type) <> ''), "
    "CONSTRAINT ck_outbox_events_aggregate_version CHECK (aggregate_version > 0), "
    "CONSTRAINT ck_outbox_events_event_type CHECK (btrim(event_type) <> ''), "
    "CONSTRAINT ck_outbox_events_publish_attempts CHECK (publish_attempts >= 0), "
    "CONSTRAINT ck_outbox_events_published_after_creation "
    "CHECK (published_at IS NULL OR published_at >= created_at), "
    "CONSTRAINT fk_outbox_events_tenant FOREIGN KEY (tenant_id) "
    "REFERENCES tenants (id) ON DELETE RESTRICT, "
    "CONSTRAINT uq_outbox_events_tenant_id UNIQUE (tenant_id, id), "
    "CONSTRAINT uq_outbox_events_tenant_aggregate_version "
    "UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version)"
    ")",
    "CREATE FUNCTION reject_publishing_targets_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF TG_OP = 'DELETE' OR "
    "(OLD.tenant_id, OLD.provider, OLD.target_reference, OLD.created_at) IS DISTINCT FROM "
    "(NEW.tenant_id, NEW.provider, NEW.target_reference, NEW.created_at) THEN "
    "RAISE EXCEPTION 'publishing_targets provenance is immutable'; END IF; "
    "RETURN NEW; END; $$",
    "CREATE TRIGGER trg_publishing_targets_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON publishing_targets FOR EACH ROW "
    "EXECUTE FUNCTION reject_publishing_targets_provenance_mutation()",
    "CREATE FUNCTION reject_publications_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF TG_OP = 'DELETE' OR "
    "(OLD.tenant_id, OLD.project_id, OLD.episode_id, OLD.audio_master_id, "
    "OLD.publishing_target_id, OLD.idempotency_key, OLD.package_sha256, OLD.provider, "
    "OLD.provider_request_id, OLD.created_at) IS DISTINCT FROM "
    "(NEW.tenant_id, NEW.project_id, NEW.episode_id, NEW.audio_master_id, "
    "NEW.publishing_target_id, NEW.idempotency_key, NEW.package_sha256, NEW.provider, "
    "NEW.provider_request_id, NEW.created_at) THEN "
    "RAISE EXCEPTION 'publications provenance is immutable'; END IF; "
    "RETURN NEW; END; $$",
    "CREATE TRIGGER trg_publications_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON publications FOR EACH ROW "
    "EXECUTE FUNCTION reject_publications_provenance_mutation()",
    "CREATE FUNCTION enforce_publication_approval_consumption() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF TG_OP = 'DELETE' OR "
    "(OLD.tenant_id, OLD.publication_id, OLD.operation, OLD.approval_reference, "
    "OLD.approved_at, OLD.expires_at, OLD.created_at) IS DISTINCT FROM "
    "(NEW.tenant_id, NEW.publication_id, NEW.operation, NEW.approval_reference, "
    "NEW.approved_at, NEW.expires_at, NEW.created_at) OR "
    "OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL OR "
    "NEW.consumed_at < OLD.approved_at OR NEW.consumed_at > OLD.expires_at THEN "
    "RAISE EXCEPTION 'publication approval may be consumed once before expiry'; END IF; "
    "RETURN NEW; END; $$",
    "CREATE TRIGGER trg_publication_approvals_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON publication_approvals FOR EACH ROW "
    "EXECUTE FUNCTION enforce_publication_approval_consumption()",
    "CREATE FUNCTION reject_usage_events_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'usage_events are immutable evidence'; END; $$",
    "CREATE TRIGGER trg_usage_events_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON usage_events FOR EACH ROW "
    "EXECUTE FUNCTION reject_usage_events_provenance_mutation()",
    "CREATE FUNCTION reject_provider_costs_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'provider_costs are immutable evidence'; END; $$",
    "CREATE TRIGGER trg_provider_costs_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON provider_costs FOR EACH ROW "
    "EXECUTE FUNCTION reject_provider_costs_provenance_mutation()",
    "CREATE FUNCTION reject_outbox_events_provenance_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "IF TG_OP = 'DELETE' OR "
    "(OLD.tenant_id, OLD.aggregate_type, OLD.aggregate_id, OLD.aggregate_version, "
    "OLD.event_type, OLD.payload, OLD.created_at, OLD.available_at) IS DISTINCT FROM "
    "(NEW.tenant_id, NEW.aggregate_type, NEW.aggregate_id, NEW.aggregate_version, "
    "NEW.event_type, NEW.payload, NEW.created_at, NEW.available_at) OR "
    "NEW.publish_attempts < OLD.publish_attempts OR "
    "(OLD.published_at IS NOT NULL AND NEW.published_at IS DISTINCT FROM OLD.published_at) THEN "
    "RAISE EXCEPTION 'outbox event provenance is immutable'; END IF; "
    "RETURN NEW; END; $$",
    "CREATE TRIGGER trg_outbox_events_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON outbox_events FOR EACH ROW "
    "EXECUTE FUNCTION reject_outbox_events_provenance_mutation()",
    "CREATE INDEX ix_publishing_targets_tenant_id ON publishing_targets (tenant_id)",
    "CREATE INDEX ix_publications_tenant_episode_id ON publications (tenant_id, episode_id)",
    "CREATE INDEX ix_publication_approvals_tenant_publication_id "
    "ON publication_approvals (tenant_id, publication_id)",
    "CREATE INDEX ix_usage_events_tenant_publication_id ON usage_events (tenant_id, publication_id)",
    "CREATE INDEX ix_provider_costs_tenant_usage_event_id "
    "ON provider_costs (tenant_id, usage_event_id)",
    "CREATE INDEX ix_outbox_events_tenant_unpublished "
    "ON outbox_events (tenant_id, available_at) WHERE published_at IS NULL",
)


_EPISODE_SNAPSHOT_STATEMENTS = (
    "ALTER TABLE episodes ADD COLUMN profile_name TEXT",
    "ALTER TABLE episodes ADD COLUMN source_sha256 CHAR(64)",
    "ALTER TABLE episodes ADD COLUMN source_bytes BIGINT",
    "ALTER TABLE episodes ADD COLUMN request_fingerprint CHAR(64)",
    "ALTER TABLE episodes ADD COLUMN state TEXT",
    "ALTER TABLE episodes ADD COLUMN qa_evidence JSONB",
    "ALTER TABLE episodes ADD COLUMN package_sha256 CHAR(64)",
    "ALTER TABLE episodes ADD COLUMN failure_evidence JSONB",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_profile_name_nonempty "
    "CHECK (profile_name IS NOT NULL AND btrim(profile_name) <> '') NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_source_sha256 "
    "CHECK (source_sha256 IS NOT NULL AND source_sha256 ~ '^[0-9a-f]{64}$') NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_source_bytes_positive "
    "CHECK (source_bytes IS NOT NULL AND source_bytes > 0) NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_request_fingerprint "
    "CHECK (request_fingerprint IS NOT NULL AND request_fingerprint ~ '^[0-9a-f]{64}$') NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_state "
    "CHECK (state IS NOT NULL AND state IN ('validated', 'scripted', 'rendered', "
    "'qa_passed', 'packaged', 'published', 'failed')) NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_package_sha256 "
    "CHECK (package_sha256 IS NULL OR package_sha256 ~ '^[0-9a-f]{64}$') NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_idempotency_key_nonempty "
    "CHECK (btrim(idempotency_key) <> '') NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_qa_evidence_object "
    "CHECK (qa_evidence IS NULL OR jsonb_typeof(qa_evidence) = 'object') NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_failure_evidence_object "
    "CHECK (failure_evidence IS NULL OR jsonb_typeof(failure_evidence) = 'object') NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_qa_passed_requires_evidence "
    "CHECK (state <> 'qa_passed' OR (qa_evidence IS NOT NULL AND "
    "jsonb_typeof(qa_evidence) = 'object')) NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_failed_requires_evidence "
    "CHECK (state <> 'failed' OR (failure_evidence IS NOT NULL AND "
    "jsonb_typeof(failure_evidence) = 'object')) NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT ck_episodes_packaged_requires_checksum "
    "CHECK (state NOT IN ('packaged', 'published') OR package_sha256 IS NOT NULL) NOT VALID",
    "ALTER TABLE episodes ADD CONSTRAINT fk_episodes_tenant_source_sha256 "
    "FOREIGN KEY (tenant_id, source_sha256) "
    "REFERENCES source_documents (tenant_id, sha256) ON DELETE RESTRICT NOT VALID",
)

_PUBLICATION_APPROVAL_METADATA_STATEMENTS = (
    "ALTER TABLE publication_approvals ADD COLUMN actor_id TEXT",
    "ALTER TABLE publication_approvals ADD COLUMN nonce_sha256 CHAR(64)",
    "ALTER TABLE publication_approvals ADD CONSTRAINT "
    "ck_publication_approvals_actor_id_nonempty "
    "CHECK (actor_id IS NOT NULL AND btrim(actor_id) <> '') NOT VALID",
    "ALTER TABLE publication_approvals ADD CONSTRAINT "
    "ck_publication_approvals_nonce_sha256_lower_hex "
    "CHECK (nonce_sha256 IS NOT NULL AND nonce_sha256 ~ '^[0-9a-f]{64}$') "
    "NOT VALID",
    "CREATE FUNCTION reject_publication_approval_evidence_mutation() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'publication approval authorization evidence is immutable'; "
    "END; $$",
    "CREATE TRIGGER trg_publication_approvals_evidence_immutable "
    "BEFORE UPDATE ON publication_approvals FOR EACH ROW "
    "WHEN (OLD.actor_id IS DISTINCT FROM NEW.actor_id "
    "OR OLD.nonce_sha256 IS DISTINCT FROM NEW.nonce_sha256) "
    "EXECUTE FUNCTION reject_publication_approval_evidence_mutation()",
    "COMMENT ON TABLE publication_approvals IS "
    "'D11 legacy rows may remain NULL until actor and nonce backfill; "
    "after backfill run VALIDATE CONSTRAINT before enforcing NOT NULL.'",
)


def _migration_checksum(statements: tuple[str, ...]) -> str:
    """Return the immutable runner checksum for ordered SQL statements."""
    return sha256("\n".join(statements).encode()).hexdigest()


TENANCY_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="003",
    checksum=_migration_checksum(_TENANCY_STATEMENTS),
    statements=_TENANCY_STATEMENTS,
)
"""D03 PostgreSQL tenant and project schema."""

EPISODE_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="004",
    checksum=_migration_checksum(_EPISODE_STATEMENTS),
    statements=_EPISODE_STATEMENTS,
)
"""D04 PostgreSQL episode, version, and source-document schema."""

CONTENT_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="005",
    checksum=_migration_checksum(_CONTENT_STATEMENTS),
    statements=_CONTENT_STATEMENTS,
)
"""D05 PostgreSQL content, profile, and speaker schema."""

VOICE_PRONUNCIATION_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="006",
    checksum=_migration_checksum(_VOICE_PRONUNCIATION_STATEMENTS),
    statements=_VOICE_PRONUNCIATION_STATEMENTS,
)
"""D06 PostgreSQL voice-consent and pronunciation schema."""

RENDER_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="007",
    checksum=_migration_checksum(_RENDER_STATEMENTS),
    statements=_RENDER_STATEMENTS,
)
"""D07 PostgreSQL render workflow and candidate provenance schema."""

AUDIO_QA_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="008",
    checksum=_migration_checksum(_AUDIO_QA_STATEMENTS),
    statements=_AUDIO_QA_STATEMENTS,
)
"""D08 PostgreSQL transcript, QA, and audio-master evidence schema."""

AUDIO_MASTER_METADATA_DIGEST_MIGRATION = PostgresCatalogMigration(
    migration_id="009",
    checksum=_migration_checksum(_AUDIO_MASTER_METADATA_DIGEST_STATEMENTS),
    statements=_AUDIO_MASTER_METADATA_DIGEST_STATEMENTS,
)
"""D08 correction binding audio-master metadata to its canonical SHA-256 digest."""

PUBLISHING_METERING_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="010",
    checksum=_migration_checksum(_PUBLISHING_METERING_STATEMENTS),
    statements=_PUBLISHING_METERING_STATEMENTS,
)
"""D09 publishing authorization, provider metering, and tenant outbox schema."""

EPISODE_SNAPSHOT_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="011",
    checksum=_migration_checksum(_EPISODE_SNAPSHOT_STATEMENTS),
    statements=_EPISODE_SNAPSHOT_STATEMENTS,
)
"""D10 episode lifecycle snapshot schema for new production rows."""

PUBLICATION_APPROVAL_METADATA_MIGRATION = PostgresCatalogMigration(
    migration_id="012",
    checksum=_migration_checksum(_PUBLICATION_APPROVAL_METADATA_STATEMENTS),
    statements=_PUBLICATION_APPROVAL_METADATA_STATEMENTS,
)
"""D11 additive actor and nonce metadata for publication approvals."""

_USAGE_LEDGER_STATEMENTS = (
    "ALTER TABLE usage_events ADD COLUMN project_id UUID",
    "ALTER TABLE usage_events ADD COLUMN job_id UUID",
    "ALTER TABLE usage_events ADD COLUMN usage JSONB",
    "ALTER TABLE usage_events ALTER COLUMN publication_id DROP NOT NULL",
    "ALTER TABLE usage_events DROP CONSTRAINT ck_usage_events_costs",
    "ALTER TABLE usage_events ALTER COLUMN estimated_cost TYPE TEXT "
    "USING estimated_cost::TEXT",
    "ALTER TABLE usage_events ALTER COLUMN reconciled_cost TYPE TEXT "
    "USING reconciled_cost::TEXT",
    "ALTER TABLE usage_events DISABLE TRIGGER trg_usage_events_provenance_immutable",
    "UPDATE usage_events SET project_id = publications.project_id "
    "FROM publications WHERE usage_events.tenant_id = publications.tenant_id "
    "AND usage_events.publication_id = publications.id",
    "UPDATE usage_events SET usage = jsonb_build_object(unit_type, units) "
    "WHERE usage IS NULL",
    "UPDATE usage_events SET job_id = candidates.job_id FROM ("
    "SELECT publications.tenant_id, publications.id AS publication_id, "
    "(array_agg(render_jobs.id ORDER BY render_jobs.id))[1] AS job_id "
    "FROM publications JOIN render_jobs "
    "ON render_jobs.tenant_id = publications.tenant_id "
    "AND render_jobs.project_id = publications.project_id "
    "AND render_jobs.episode_id = publications.episode_id GROUP BY "
    "publications.tenant_id, publications.id HAVING count(*) = 1"
    ") AS candidates WHERE usage_events.tenant_id = candidates.tenant_id "
    "AND usage_events.publication_id = candidates.publication_id",
    "ALTER TABLE usage_events ADD CONSTRAINT ck_usage_events_project_id "
    "CHECK (project_id IS NOT NULL) NOT VALID",
    "ALTER TABLE usage_events ADD CONSTRAINT ck_usage_events_job_id "
    "CHECK (job_id IS NOT NULL) NOT VALID",
    "ALTER TABLE usage_events ADD CONSTRAINT ck_usage_events_usage_object "
    "CHECK (usage IS NOT NULL AND jsonb_typeof(usage) = 'object') NOT VALID",
    "ALTER TABLE usage_events ADD CONSTRAINT ck_usage_events_costs CHECK ("
    "CASE WHEN estimated_cost ~ '^(0|[1-9][0-9]*)(\\.[0-9]+)?$' "
    "THEN estimated_cost::NUMERIC >= 0 ELSE FALSE END AND "
    "CASE WHEN reconciled_cost IS NULL THEN TRUE WHEN reconciled_cost ~ "
    "'^(0|[1-9][0-9]*)(\\.[0-9]+)?$' THEN reconciled_cost::NUMERIC >= 0 "
    "ELSE FALSE END) NOT VALID",
    "ALTER TABLE usage_events ADD CONSTRAINT fk_usage_events_tenant_project "
    "FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) "
    "ON DELETE RESTRICT NOT VALID",
    "ALTER TABLE render_jobs ADD CONSTRAINT uq_render_jobs_tenant_project_id "
    "UNIQUE (tenant_id, project_id, id)",
    "ALTER TABLE usage_events ADD CONSTRAINT fk_usage_events_tenant_job "
    "FOREIGN KEY (tenant_id, project_id, job_id) "
    "REFERENCES render_jobs (tenant_id, project_id, id) ON DELETE RESTRICT NOT VALID",
    "ALTER TABLE usage_events ENABLE TRIGGER trg_usage_events_provenance_immutable",
)

USAGE_LEDGER_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="013",
    checksum=_migration_checksum(_USAGE_LEDGER_STATEMENTS),
    statements=_USAGE_LEDGER_STATEMENTS,
)
"""D12 tenant/project/job usage ledger scope and normalized usage evidence."""

_TENANT_RLS_TABLES = (
    "projects",
    "source_documents",
    "episodes",
    "episode_versions",
    "scripts",
    "script_versions",
    "profiles",
    "speakers",
    "voice_assets",
    "voice_profiles",
    "voice_consents",
    "pronunciation_lexicons",
    "pronunciation_entries",
    "render_jobs",
    "render_segments",
    "render_candidates",
    "transcripts",
    "qa_results",
    "audio_masters",
    "publishing_targets",
    "publications",
    "publication_approvals",
    "usage_events",
    "provider_costs",
    "outbox_events",
)

_TENANT_RLS_STATEMENTS = (
    "CREATE FUNCTION poddown_current_tenant_id() RETURNS UUID STABLE "
    "LANGUAGE sql AS $$ SELECT "
    "NULLIF(current_setting('app.tenant_id', true), '')::UUID $$",
    *(
        statement
        for table in _TENANT_RLS_TABLES
        for statement in (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            "USING (tenant_id = poddown_current_tenant_id()) "
            "WITH CHECK (tenant_id = poddown_current_tenant_id())",
        )
    ),
    "COMMENT ON FUNCTION poddown_current_tenant_id() IS "
    "'D13 requires SET LOCAL app.tenant_id for every PostgreSQL transaction; "
    "an unset or empty context returns NULL so RLS denies access. SQLite local "
    "and test mode do not implement PostgreSQL RLS.'",
)

TENANT_RLS_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="014",
    checksum=_migration_checksum(_TENANT_RLS_STATEMENTS),
    statements=_TENANT_RLS_STATEMENTS,
)
"""D13 PostgreSQL tenant RLS defense-in-depth for all tenant-owned tables."""

_OUTBOX_LEASE_STATEMENTS = (
    "ALTER TABLE outbox_events ADD COLUMN claim_token UUID",
    "ALTER TABLE outbox_events ADD COLUMN lease_until TIMESTAMPTZ",
    "ALTER TABLE outbox_events ADD CONSTRAINT ck_outbox_events_lease_pair "
    "CHECK ((claim_token IS NULL) = (lease_until IS NULL))",
    "DROP TRIGGER trg_outbox_events_provenance_immutable ON outbox_events",
    "CREATE OR REPLACE FUNCTION reject_outbox_events_provenance_mutation() "
    "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
    "IF TG_OP = 'DELETE' OR "
    "(OLD.id, OLD.tenant_id, OLD.aggregate_type, OLD.aggregate_id, "
    "OLD.aggregate_version, OLD.event_type, OLD.payload, OLD.created_at) "
    "IS DISTINCT FROM "
    "(NEW.id, NEW.tenant_id, NEW.aggregate_type, NEW.aggregate_id, "
    "NEW.aggregate_version, NEW.event_type, NEW.payload, NEW.created_at) THEN "
    "RAISE EXCEPTION 'outbox event provenance is immutable'; END IF; "
    "IF NEW.publish_attempts < OLD.publish_attempts OR "
    "(OLD.published_at IS NOT NULL AND NEW.published_at IS DISTINCT FROM "
    "OLD.published_at) THEN "
    "RAISE EXCEPTION 'outbox event delivery state is invalid'; END IF; "
    "IF NEW.publish_attempts IS DISTINCT FROM OLD.publish_attempts AND NOT "
    "(NEW.publish_attempts = OLD.publish_attempts + 1 AND "
    "NEW.available_at IS NOT DISTINCT FROM OLD.available_at AND "
    "NEW.published_at IS NOT DISTINCT FROM OLD.published_at AND "
    "NEW.claim_token IS NOT NULL AND NEW.lease_until > CURRENT_TIMESTAMP AND "
    "((OLD.claim_token IS NULL AND OLD.lease_until IS NULL) OR "
    "OLD.lease_until <= CURRENT_TIMESTAMP)) THEN "
    "RAISE EXCEPTION 'outbox event claim is invalid'; END IF; "
    "IF NEW.available_at IS DISTINCT FROM OLD.available_at AND NOT "
    "(NEW.publish_attempts = OLD.publish_attempts AND "
    "NEW.published_at IS NOT DISTINCT FROM OLD.published_at AND "
    "OLD.claim_token IS NOT NULL AND OLD.lease_until > CURRENT_TIMESTAMP AND "
    "NEW.claim_token IS NULL AND NEW.lease_until IS NULL AND "
    "NEW.available_at > CURRENT_TIMESTAMP) THEN "
    "RAISE EXCEPTION 'outbox event retry release is invalid'; END IF; "
    "IF (NEW.claim_token IS DISTINCT FROM OLD.claim_token OR "
    "NEW.lease_until IS DISTINCT FROM OLD.lease_until) AND NOT "
    "((NEW.publish_attempts = OLD.publish_attempts + 1 AND "
    "NEW.available_at IS NOT DISTINCT FROM OLD.available_at AND "
    "NEW.published_at IS NOT DISTINCT FROM OLD.published_at AND "
    "NEW.claim_token IS NOT NULL AND NEW.lease_until > CURRENT_TIMESTAMP AND "
    "((OLD.claim_token IS NULL AND OLD.lease_until IS NULL) OR "
    "OLD.lease_until <= CURRENT_TIMESTAMP)) OR "
    "(NEW.publish_attempts = OLD.publish_attempts AND "
    "NEW.published_at IS NOT DISTINCT FROM OLD.published_at AND "
    "NEW.available_at IS DISTINCT FROM OLD.available_at AND "
    "OLD.claim_token IS NOT NULL AND OLD.lease_until > CURRENT_TIMESTAMP AND "
    "NEW.claim_token IS NULL AND NEW.lease_until IS NULL AND "
    "NEW.available_at > CURRENT_TIMESTAMP)) THEN "
    "RAISE EXCEPTION 'outbox event lease state is invalid'; END IF; "
    "RETURN NEW; END; $$",
    "CREATE TRIGGER trg_outbox_events_provenance_immutable "
    "BEFORE UPDATE OR DELETE ON outbox_events FOR EACH ROW "
    "EXECUTE FUNCTION reject_outbox_events_provenance_mutation()",
)

OUTBOX_LEASE_SCHEMA_MIGRATION = PostgresCatalogMigration(
    migration_id="015",
    checksum=_migration_checksum(_OUTBOX_LEASE_STATEMENTS),
    statements=_OUTBOX_LEASE_STATEMENTS,
)
"""D16 additive outbox lease fields and sanctioned delivery-state transitions."""

POSTGRESQL_MIGRATIONS = (
    TENANCY_SCHEMA_MIGRATION,
    EPISODE_SCHEMA_MIGRATION,
    CONTENT_SCHEMA_MIGRATION,
    VOICE_PRONUNCIATION_SCHEMA_MIGRATION,
    RENDER_SCHEMA_MIGRATION,
    AUDIO_QA_SCHEMA_MIGRATION,
    AUDIO_MASTER_METADATA_DIGEST_MIGRATION,
    PUBLISHING_METERING_SCHEMA_MIGRATION,
    EPISODE_SNAPSHOT_SCHEMA_MIGRATION,
    PUBLICATION_APPROVAL_METADATA_MIGRATION,
    USAGE_LEDGER_SCHEMA_MIGRATION,
    TENANT_RLS_SCHEMA_MIGRATION,
    OUTBOX_LEASE_SCHEMA_MIGRATION,
)
"""Ordered D03-D16 schema-contract catalog used by opt-in integration tests.

The repository runtime intentionally bootstraps ``MIGRATIONS`` above because
that catalog contains the durable receipt, evidence, object-reference, and
legacy outbox tables used by the current PostgreSQL adapters.  This catalog is
kept as the forward-only contract for the newer production data-plane schema
tests until those adapters and tables are migrated as one bounded change.
"""


def validate_tenancy_schema_migration(migration: PostgresCatalogMigration) -> None:
    """Reject a D03 definition that no longer preserves tenancy invariants."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "CREATE TABLE tenants",
        "CREATE TABLE projects",
        "id UUID CONSTRAINT pk_tenants PRIMARY KEY",
        "id UUID CONSTRAINT pk_projects PRIMARY KEY",
        "substring(id::text FROM 15 FOR 1) = '7'",
        "CONSTRAINT uq_tenants_slug UNIQUE (slug)",
        "tenant_id UUID NOT NULL",
        "CONSTRAINT fk_projects_tenant FOREIGN KEY (tenant_id) "
        "REFERENCES tenants (id) ON DELETE RESTRICT",
        "CONSTRAINT uq_projects_tenant_slug UNIQUE (tenant_id, slug)",
        "CREATE INDEX ix_projects_tenant_id ON projects (tenant_id)",
    )
    if migration.migration_id != "003":
        raise ValueError("D03 tenancy migration must use ID '003'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError("D03 tenancy migration checksum does not match its statements")
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D03 tenancy migration is missing a required tenancy constraint"
        )
    if statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") != 4:
        raise ValueError("D03 tenancy migration must declare four UTC timestamps")


validate_tenancy_schema_migration(TENANCY_SCHEMA_MIGRATION)


def validate_episode_schema_migration(migration: PostgresCatalogMigration) -> None:
    """Reject a D04 definition that loses source, immutability, or version state."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "ALTER TABLE projects ADD CONSTRAINT uq_projects_tenant_id "
        "UNIQUE (tenant_id, id)",
        "CREATE TABLE source_documents",
        "CREATE TABLE episodes",
        "CREATE TABLE episode_versions",
        "CONSTRAINT uq_source_documents_tenant_sha256 UNIQUE (tenant_id, sha256)",
        "CONSTRAINT uq_source_documents_tenant_id_sha256 "
        "UNIQUE (tenant_id, id, sha256)",
        "CONSTRAINT ck_source_documents_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$')",
        "CONSTRAINT uq_episodes_tenant_idempotency_key "
        "UNIQUE (tenant_id, idempotency_key)",
        "CONSTRAINT ck_episodes_version CHECK (version > 0)",
        "CONSTRAINT fk_episodes_tenant_project FOREIGN KEY (tenant_id, project_id) "
        "REFERENCES projects (tenant_id, id) ON DELETE RESTRICT",
        "CONSTRAINT uq_episode_versions_episode_version UNIQUE (episode_id, version)",
        "CONSTRAINT ck_episode_versions_version CHECK (version > 0)",
        "CONSTRAINT fk_episode_versions_tenant_episode "
        "FOREIGN KEY (tenant_id, episode_id) REFERENCES episodes (tenant_id, id) "
        "ON DELETE RESTRICT",
        "CONSTRAINT fk_episode_versions_tenant_source_document "
        "FOREIGN KEY (tenant_id, source_document_id) "
        "REFERENCES source_documents (tenant_id, id) ON DELETE RESTRICT",
        "CONSTRAINT fk_episode_versions_tenant_source_document_hash "
        "FOREIGN KEY (tenant_id, source_document_id, source_sha256) "
        "REFERENCES source_documents (tenant_id, id, sha256) ON DELETE RESTRICT",
        "CREATE FUNCTION reject_source_documents_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'source_documents are immutable'; END; $$",
        "CREATE TRIGGER trg_source_documents_immutable "
        "BEFORE UPDATE OR DELETE ON source_documents FOR EACH ROW "
        "EXECUTE FUNCTION reject_source_documents_mutation()",
        "CREATE FUNCTION reject_episode_versions_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'episode_versions are immutable'; END; $$",
        "CREATE TRIGGER trg_episode_versions_immutable "
        "BEFORE UPDATE OR DELETE ON episode_versions FOR EACH ROW "
        "EXECUTE FUNCTION reject_episode_versions_mutation()",
        "CREATE INDEX ix_source_documents_tenant_id ON source_documents (tenant_id)",
        "CREATE INDEX ix_episodes_tenant_project_id "
        "ON episodes (tenant_id, project_id)",
        "CREATE INDEX ix_episode_versions_tenant_episode_id "
        "ON episode_versions (tenant_id, episode_id)",
    )
    if migration.migration_id != "004":
        raise ValueError("D04 episode migration must use ID '004'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError("D04 episode migration checksum does not match its statements")
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D04 episode migration is missing a required schema constraint"
        )
    if statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") != 6:
        raise ValueError("D04 episode migration must declare six UTC timestamps")


validate_episode_schema_migration(EPISODE_SCHEMA_MIGRATION)


def validate_content_schema_migration(migration: PostgresCatalogMigration) -> None:
    """Reject a D05 definition that loses content or profile integrity."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "CREATE TABLE scripts",
        "CREATE TABLE script_versions",
        "CREATE TABLE profiles",
        "CREATE TABLE speakers",
        "CONSTRAINT uq_scripts_tenant_sha256 UNIQUE (tenant_id, sha256)",
        "CONSTRAINT uq_scripts_tenant_id_sha256 UNIQUE (tenant_id, id, sha256)",
        "ALTER TABLE episode_versions ADD CONSTRAINT "
        "fk_episode_versions_tenant_script_hash "
        "FOREIGN KEY (tenant_id, script_sha256) "
        "REFERENCES scripts (tenant_id, sha256) ON DELETE RESTRICT",
        "CONSTRAINT fk_script_versions_tenant_script_hash "
        "FOREIGN KEY (tenant_id, script_id, script_sha256) "
        "REFERENCES scripts (tenant_id, id, sha256) ON DELETE RESTRICT",
        "CONSTRAINT uq_script_versions_script_version UNIQUE (script_id, version)",
        "CONSTRAINT ck_script_versions_version CHECK (version > 0)",
        "CONSTRAINT uq_profiles_tenant_profile_version "
        "UNIQUE (tenant_id, profile_id, version)",
        "CONSTRAINT fk_speakers_tenant_profile "
        "FOREIGN KEY (tenant_id, profile_id) REFERENCES profiles (tenant_id, id) "
        "ON DELETE RESTRICT",
        "CONSTRAINT uq_speakers_profile_speaker_id UNIQUE (profile_id, speaker_id)",
        "CREATE TRIGGER trg_scripts_immutable BEFORE UPDATE OR DELETE ON scripts",
        "CREATE TRIGGER trg_script_versions_immutable "
        "BEFORE UPDATE OR DELETE ON script_versions",
        "CREATE INDEX ix_script_versions_tenant_script_id "
        "ON script_versions (tenant_id, script_id)",
        "CREATE INDEX ix_profiles_tenant_id ON profiles (tenant_id)",
        "CREATE INDEX ix_speakers_tenant_profile_id "
        "ON speakers (tenant_id, profile_id)",
    )
    if migration.migration_id != "005":
        raise ValueError("D05 content migration must use ID '005'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError("D05 content migration checksum does not match its statements")
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D05 content migration is missing a required schema constraint"
        )
    if statements.count("substring(id::text FROM 15 FOR 1) = '7'") != 4:
        raise ValueError("D05 content migration must declare four UUIDv7 identifiers")
    if statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") != 8:
        raise ValueError("D05 content migration must declare eight UTC timestamps")


validate_content_schema_migration(CONTENT_SCHEMA_MIGRATION)


def validate_voice_pronunciation_schema_migration(
    migration: PostgresCatalogMigration,
) -> None:
    """Reject a D06 definition that loses voice-rights or lexicon integrity."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "CREATE TABLE voice_assets",
        "CONSTRAINT ck_voice_assets_provider "
        "CHECK (provider IN ('local', 'elevenlabs', 'openai'))",
        "CREATE TABLE voice_profiles",
        "ALTER TABLE speakers ADD COLUMN voice_profile_id UUID",
        "ALTER TABLE speakers ADD CONSTRAINT fk_speakers_tenant_voice_profile "
        "FOREIGN KEY (tenant_id, voice_profile_id) "
        "REFERENCES voice_profiles (tenant_id, id) ON DELETE RESTRICT",
        "CREATE TABLE voice_consents",
        "allowed_providers TEXT[] NOT NULL",
        "CONSTRAINT ck_voice_consents_allowed_providers "
        "CHECK (cardinality(allowed_providers) > 0 "
        "AND allowed_providers <@ ARRAY['local', 'elevenlabs', 'openai']::TEXT[])",
        "evidence_reference TEXT NOT NULL",
        "valid_from TIMESTAMPTZ NOT NULL",
        "valid_until TIMESTAMPTZ NOT NULL",
        "revoked_at TIMESTAMPTZ",
        "CONSTRAINT ck_voice_consents_validity_window CHECK (valid_until > valid_from)",
        "CONSTRAINT ck_voice_consents_revocation "
        "CHECK (revoked_at IS NULL OR revoked_at >= valid_from)",
        "CONSTRAINT fk_voice_consents_tenant_voice_asset "
        "FOREIGN KEY (tenant_id, voice_asset_id) "
        "REFERENCES voice_assets (tenant_id, id) ON DELETE RESTRICT",
        "CREATE TABLE pronunciation_lexicons",
        "CONSTRAINT ck_pronunciation_lexicons_scope_precedence "
        "CHECK ((scope = 'episode' AND precedence = 1) "
        "OR (scope = 'project' AND precedence = 2) "
        "OR (scope = 'domain' AND precedence = 3) "
        "OR (scope = 'global' AND precedence = 4))",
        "CONSTRAINT uq_pronunciation_lexicons_tenant_scope_version "
        "UNIQUE (tenant_id, scope, scope_reference, version)",
        "CREATE TABLE pronunciation_entries",
        "normalized_key TEXT NOT NULL",
        "CONSTRAINT uq_pronunciation_entries_lexicon_normalized_key "
        "UNIQUE (lexicon_id, normalized_key)",
        "CONSTRAINT fk_pronunciation_entries_tenant_lexicon "
        "FOREIGN KEY (tenant_id, lexicon_id) "
        "REFERENCES pronunciation_lexicons (tenant_id, id) ON DELETE RESTRICT",
        "CREATE INDEX ix_voice_consents_tenant_voice_asset_id "
        "ON voice_consents (tenant_id, voice_asset_id)",
        "CREATE INDEX ix_pronunciation_entries_tenant_lexicon_id "
        "ON pronunciation_entries (tenant_id, lexicon_id)",
    )
    if migration.migration_id != "006":
        raise ValueError("D06 voice and pronunciation migration must use ID '006'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D06 voice and pronunciation migration checksum does not match "
            "its statements"
        )
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D06 voice and pronunciation migration is missing a required "
            "schema constraint"
        )
    if statements.count("substring(id::text FROM 15 FOR 1) = '7'") != 5:
        raise ValueError("D06 migration must declare five UUIDv7 identifiers")
    if statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") != 10:
        raise ValueError("D06 migration must declare ten UTC timestamps")


validate_voice_pronunciation_schema_migration(VOICE_PRONUNCIATION_SCHEMA_MIGRATION)


def validate_render_schema_migration(migration: PostgresCatalogMigration) -> None:
    """Reject a D07 definition that loses render workflow provenance."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "ALTER TABLE episodes ADD CONSTRAINT uq_episodes_tenant_id_project_id "
        "UNIQUE (tenant_id, id, project_id)",
        "ALTER TABLE episode_versions ADD CONSTRAINT uq_episode_versions_tenant_id ",
        "ALTER TABLE episode_versions ADD CONSTRAINT "
        "uq_episode_versions_tenant_id_script_sha256 ",
        "ALTER TABLE episode_versions ADD CONSTRAINT "
        "uq_episode_versions_tenant_id_episode_id_script_sha256 ",
        "CREATE TABLE render_jobs",
        "CREATE TABLE render_segments",
        "CREATE TABLE render_candidates",
        "CONSTRAINT uq_render_jobs_tenant_workflow_id UNIQUE (tenant_id, workflow_id)",
        "CONSTRAINT fk_render_jobs_tenant_project FOREIGN KEY (tenant_id, project_id) "
        "REFERENCES projects (tenant_id, id) ON DELETE RESTRICT",
        "CONSTRAINT fk_render_jobs_tenant_episode FOREIGN KEY (tenant_id, episode_id) "
        "REFERENCES episodes (tenant_id, id) ON DELETE RESTRICT",
        "CONSTRAINT fk_render_jobs_tenant_episode_project "
        "FOREIGN KEY (tenant_id, episode_id, project_id) "
        "REFERENCES episodes (tenant_id, id, project_id) ON DELETE RESTRICT",
        "CONSTRAINT fk_render_jobs_tenant_episode_version_content "
        "FOREIGN KEY (tenant_id, episode_version_id, episode_id, content_sha256) "
        "REFERENCES episode_versions (tenant_id, id, episode_id, script_sha256) "
        "ON DELETE RESTRICT",
        "CONSTRAINT ck_render_jobs_status ",
        "CONSTRAINT ck_render_jobs_attempt CHECK (attempt > 0)",
        "CONSTRAINT uq_render_segments_job_segment_attempt "
        "UNIQUE (render_job_id, segment_id, attempt)",
        "CONSTRAINT uq_render_segments_tenant_id_job UNIQUE "
        "(tenant_id, id, render_job_id)",
        "CONSTRAINT ck_render_segments_status ",
        "CONSTRAINT uq_render_candidates_segment_take "
        "UNIQUE (render_segment_id, take_index)",
        "CONSTRAINT ck_render_candidates_take_index CHECK (take_index >= 0)",
        "provider_request_id TEXT NOT NULL",
        "provider_metadata JSONB NOT NULL",
        "cost_reference UUID NOT NULL",
        "audio_sha256 CHAR(64) NOT NULL",
        "response_sha256 CHAR(64) NOT NULL",
        "CREATE TRIGGER trg_render_jobs_provenance_immutable",
        "CREATE TRIGGER trg_render_segments_provenance_immutable",
        "CREATE TRIGGER trg_render_candidates_provenance_immutable",
        "CONSTRAINT fk_render_candidates_tenant_segment_job "
        "FOREIGN KEY (tenant_id, render_segment_id, render_job_id) "
        "REFERENCES render_segments (tenant_id, id, render_job_id) ON DELETE RESTRICT",
        "CONSTRAINT uq_render_candidates_tenant_provider_request "
        "UNIQUE (tenant_id, provider, provider_request_id)",
        "CREATE INDEX ix_render_jobs_tenant_project_episode_version_id ",
        "CREATE INDEX ix_render_segments_tenant_job_id ",
        "CREATE INDEX ix_render_candidates_tenant_segment_id ",
    )
    if migration.migration_id != "007":
        raise ValueError("D07 render migration must use ID '007'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError("D07 render migration checksum does not match its statements")
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError("D07 render migration is missing a required schema constraint")
    if statements.count("substring(id::text FROM 15 FOR 1) = '7'") != 3:
        raise ValueError("D07 migration must declare three UUIDv7 identifiers")
    if statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") != 6:
        raise ValueError("D07 migration must declare six UTC timestamps")


validate_render_schema_migration(RENDER_SCHEMA_MIGRATION)


def validate_audio_qa_schema_migration(migration: PostgresCatalogMigration) -> None:
    """Reject a D08 definition that loses transcript, QA, or master provenance."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "ALTER TABLE render_candidates ADD CONSTRAINT "
        "uq_render_candidates_tenant_id_response_audio "
        "UNIQUE (tenant_id, id, response_sha256, audio_sha256)",
        "ALTER TABLE episode_versions ADD CONSTRAINT "
        "uq_episode_versions_tenant_id_source_sha256 "
        "UNIQUE (tenant_id, id, source_sha256)",
        "CREATE TABLE transcripts",
        "render_response_sha256 CHAR(64) NOT NULL",
        "CONSTRAINT ck_transcripts_evidence_kind "
        "CHECK (evidence_kind IN ('provider-asr', 'script-derived'))",
        "CONSTRAINT ck_transcripts_provider_evidence CHECK (",
        "CONSTRAINT fk_transcripts_tenant_candidate_provenance "
        "FOREIGN KEY (tenant_id, render_candidate_id, render_response_sha256, audio_sha256) "
        "REFERENCES render_candidates (tenant_id, id, response_sha256, audio_sha256) "
        "ON DELETE RESTRICT",
        "CREATE TABLE qa_results",
        "gate_evidence JSONB NOT NULL",
        "critical_token_accuracy NUMERIC(5,4) NOT NULL",
        "CONSTRAINT ck_qa_results_pass_requires_perfect_critical_tokens "
        "CHECK (gate_status <> 'pass' OR critical_token_accuracy = 1.0000)",
        "CONSTRAINT fk_qa_results_tenant_transcript_audio "
        "FOREIGN KEY (tenant_id, transcript_id, audio_sha256) "
        "REFERENCES transcripts (tenant_id, id, audio_sha256) ON DELETE RESTRICT",
        "CREATE TABLE audio_masters",
        "mastering_metadata JSONB NOT NULL",
        "mastering_metadata_sha256 CHAR(64) NOT NULL",
        "CONSTRAINT fk_audio_masters_tenant_episode_version_source "
        "FOREIGN KEY (tenant_id, episode_version_id, source_sha256) "
        "REFERENCES episode_versions (tenant_id, id, source_sha256) ON DELETE RESTRICT",
        "CREATE TRIGGER trg_transcripts_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON transcripts",
        "CREATE TRIGGER trg_qa_results_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON qa_results",
        "CREATE TRIGGER trg_audio_masters_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON audio_masters",
        "CREATE INDEX ix_transcripts_tenant_candidate_id ",
        "CREATE INDEX ix_qa_results_tenant_transcript_id ",
        "CREATE INDEX ix_audio_masters_tenant_episode_version_id ",
    )
    if migration.migration_id != "008":
        raise ValueError(
            "D08 transcript, QA, and audio-master migration must use ID '008'"
        )
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D08 audio QA migration checksum does not match its statements"
        )
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D08 audio QA migration is missing a required schema constraint"
        )
    if statements.count("substring(id::text FROM 15 FOR 1) = '7'") != 3:
        raise ValueError("D08 migration must declare three UUIDv7 identifiers")
    if statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") != 6:
        raise ValueError("D08 migration must declare six UTC timestamps")


validate_audio_qa_schema_migration(AUDIO_QA_SCHEMA_MIGRATION)


def validate_audio_master_metadata_digest_migration(
    migration: PostgresCatalogMigration,
) -> None:
    """Reject a D08 correction that does not bind metadata to its digest."""
    required_fragments = (
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "ALTER TABLE audio_masters ADD CONSTRAINT "
        "ck_audio_masters_metadata_sha256_matches_metadata "
        "CHECK (mastering_metadata_sha256 = "
        "encode(digest(mastering_metadata::text, 'sha256'), 'hex'))",
    )
    statements = "\n".join(migration.statements)
    if migration.migration_id != "009":
        raise ValueError("D08 metadata digest correction must use ID '009'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D08 metadata digest correction checksum does not match its statements"
        )
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D08 metadata digest correction is missing a required schema constraint"
        )


validate_audio_master_metadata_digest_migration(AUDIO_MASTER_METADATA_DIGEST_MIGRATION)


def validate_publishing_metering_schema_migration(
    migration: PostgresCatalogMigration,
) -> None:
    """Reject a D09 definition that loses publishing or metering safeguards."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "CREATE TABLE publishing_targets",
        "CREATE TABLE publications",
        "CREATE TABLE publication_approvals",
        "CREATE TABLE usage_events",
        "CREATE TABLE provider_costs",
        "CREATE TABLE outbox_events",
        "CONSTRAINT uq_publishing_targets_tenant_provider_target "
        "UNIQUE (tenant_id, provider, target_reference)",
        "CONSTRAINT fk_publications_tenant_target "
        "FOREIGN KEY (tenant_id, publishing_target_id) "
        "REFERENCES publishing_targets (tenant_id, id) ON DELETE RESTRICT",
        "CONSTRAINT uq_publications_tenant_idempotency_key "
        "UNIQUE (tenant_id, idempotency_key)",
        "CONSTRAINT ck_publication_approvals_operation "
        "CHECK (operation IN ('publish', 'update', 'delete'))",
        "CONSTRAINT ck_publication_approvals_expiry CHECK (expires_at > approved_at)",
        "CONSTRAINT uq_publication_approvals_tenant_reference "
        "UNIQUE (tenant_id, approval_reference)",
        "CREATE FUNCTION enforce_publication_approval_consumption()",
        "CONSTRAINT fk_usage_events_tenant_publication "
        "FOREIGN KEY (tenant_id, publication_id) "
        "REFERENCES publications (tenant_id, id) ON DELETE RESTRICT",
        "units NUMERIC(20,6) NOT NULL",
        "estimated_cost NUMERIC(20,6) NOT NULL",
        "reconciled_cost NUMERIC(20,6)",
        "CONSTRAINT uq_usage_events_tenant_provider_request "
        "UNIQUE (tenant_id, provider, provider_request_id)",
        "CONSTRAINT fk_provider_costs_tenant_usage_event "
        "FOREIGN KEY (tenant_id, usage_event_id, provider, provider_request_id) "
        "REFERENCES usage_events (tenant_id, id, provider, provider_request_id) "
        "ON DELETE RESTRICT",
        "CONSTRAINT uq_provider_costs_tenant_id UNIQUE (tenant_id, id)",
        "CONSTRAINT fk_render_candidates_tenant_cost_reference "
        "FOREIGN KEY (tenant_id, cost_reference) "
        "REFERENCES provider_costs (tenant_id, id) ON DELETE RESTRICT",
        "amount NUMERIC(20,6) NOT NULL",
        "aggregate_version BIGINT NOT NULL",
        "payload JSONB NOT NULL",
        "publish_attempts INTEGER NOT NULL DEFAULT 0",
        "published_at TIMESTAMPTZ",
        "CONSTRAINT uq_outbox_events_tenant_aggregate_version "
        "UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version)",
        "CREATE TRIGGER trg_publishing_targets_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON publishing_targets",
        "CREATE TRIGGER trg_publications_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON publications",
        "CREATE TRIGGER trg_publication_approvals_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON publication_approvals",
        "CREATE TRIGGER trg_usage_events_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON usage_events",
        "CREATE TRIGGER trg_provider_costs_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON provider_costs",
        "CREATE TRIGGER trg_outbox_events_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON outbox_events",
    )
    if migration.migration_id != "010":
        raise ValueError("D09 publishing and metering migration must use ID '010'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D09 publishing and metering migration checksum does not match its statements"
        )
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D09 publishing and metering migration is missing a required schema constraint"
        )
    if statements.count("substring(id::text FROM 15 FOR 1) = '7'") != 6:
        raise ValueError("D09 migration must declare six UUIDv7 identifiers")
    if statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") != 12:
        raise ValueError("D09 migration must declare twelve UTC timestamps")


validate_publishing_metering_schema_migration(PUBLISHING_METERING_SCHEMA_MIGRATION)


def validate_outbox_lease_schema_migration(migration: PostgresCatalogMigration) -> None:
    """Reject D16 changes that weaken outbox identity or lease ownership."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "ALTER TABLE outbox_events ADD COLUMN claim_token UUID",
        "ALTER TABLE outbox_events ADD COLUMN lease_until TIMESTAMPTZ",
        "CONSTRAINT ck_outbox_events_lease_pair "
        "CHECK ((claim_token IS NULL) = (lease_until IS NULL))",
        "DROP TRIGGER trg_outbox_events_provenance_immutable ON outbox_events",
        "CREATE OR REPLACE FUNCTION reject_outbox_events_provenance_mutation()",
        "OLD.payload, OLD.created_at",
        "NEW.claim_token IS NOT NULL AND NEW.lease_until > CURRENT_TIMESTAMP",
        "OLD.lease_until > CURRENT_TIMESTAMP AND NEW.claim_token IS NULL AND "
        "NEW.lease_until IS NULL",
        "CREATE TRIGGER trg_outbox_events_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON outbox_events",
    )
    if migration.migration_id != "015":
        raise ValueError("D16 outbox lease migration must use ID '015'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D16 outbox lease migration checksum does not match its statements"
        )
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D16 outbox lease migration is missing a required schema constraint"
        )


validate_outbox_lease_schema_migration(OUTBOX_LEASE_SCHEMA_MIGRATION)


def validate_episode_snapshot_schema_migration(
    migration: PostgresCatalogMigration,
) -> None:
    """Reject a D10 definition that loses lifecycle snapshot safeguards."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "ALTER TABLE episodes ADD COLUMN profile_name TEXT",
        "ALTER TABLE episodes ADD COLUMN source_sha256 CHAR(64)",
        "ALTER TABLE episodes ADD COLUMN source_bytes BIGINT",
        "ALTER TABLE episodes ADD COLUMN request_fingerprint CHAR(64)",
        "ALTER TABLE episodes ADD COLUMN state TEXT",
        "ALTER TABLE episodes ADD COLUMN qa_evidence JSONB",
        "ALTER TABLE episodes ADD COLUMN package_sha256 CHAR(64)",
        "ALTER TABLE episodes ADD COLUMN failure_evidence JSONB",
        "CONSTRAINT ck_episodes_profile_name_nonempty "
        "CHECK (profile_name IS NOT NULL AND btrim(profile_name) <> '') NOT VALID",
        "CONSTRAINT ck_episodes_source_sha256 "
        "CHECK (source_sha256 IS NOT NULL AND source_sha256 ~ '^[0-9a-f]{64}$') NOT VALID",
        "CONSTRAINT ck_episodes_source_bytes_positive "
        "CHECK (source_bytes IS NOT NULL AND source_bytes > 0) NOT VALID",
        "CONSTRAINT ck_episodes_request_fingerprint "
        "CHECK (request_fingerprint IS NOT NULL AND request_fingerprint ~ '^[0-9a-f]{64}$') NOT VALID",
        "CONSTRAINT ck_episodes_state "
        "CHECK (state IS NOT NULL AND state IN ('validated', 'scripted', 'rendered', "
        "'qa_passed', 'packaged', 'published', 'failed')) NOT VALID",
        "CONSTRAINT ck_episodes_package_sha256 "
        "CHECK (package_sha256 IS NULL OR package_sha256 ~ '^[0-9a-f]{64}$') NOT VALID",
        "CONSTRAINT ck_episodes_idempotency_key_nonempty "
        "CHECK (btrim(idempotency_key) <> '') NOT VALID",
        "CONSTRAINT ck_episodes_qa_evidence_object "
        "CHECK (qa_evidence IS NULL OR jsonb_typeof(qa_evidence) = 'object') NOT VALID",
        "CONSTRAINT ck_episodes_failure_evidence_object "
        "CHECK (failure_evidence IS NULL OR jsonb_typeof(failure_evidence) = 'object') NOT VALID",
        "CONSTRAINT ck_episodes_qa_passed_requires_evidence "
        "CHECK (state <> 'qa_passed' OR (qa_evidence IS NOT NULL AND "
        "jsonb_typeof(qa_evidence) = 'object')) NOT VALID",
        "CONSTRAINT ck_episodes_failed_requires_evidence "
        "CHECK (state <> 'failed' OR (failure_evidence IS NOT NULL AND "
        "jsonb_typeof(failure_evidence) = 'object')) NOT VALID",
        "CONSTRAINT ck_episodes_packaged_requires_checksum "
        "CHECK (state NOT IN ('packaged', 'published') OR package_sha256 IS NOT NULL) NOT VALID",
        "CONSTRAINT fk_episodes_tenant_source_sha256 "
        "FOREIGN KEY (tenant_id, source_sha256) "
        "REFERENCES source_documents (tenant_id, sha256) ON DELETE RESTRICT NOT VALID",
    )
    if migration.migration_id != "011":
        raise ValueError("D10 episode snapshot migration must use ID '011'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D10 episode snapshot migration checksum does not match its statements"
        )
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D10 episode snapshot migration is missing a required schema constraint"
        )


validate_episode_snapshot_schema_migration(EPISODE_SNAPSHOT_SCHEMA_MIGRATION)


def validate_publication_approval_metadata_migration(
    migration: PostgresCatalogMigration,
) -> None:
    """Reject D11 definitions that weaken approval actor or nonce evidence."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "ALTER TABLE publication_approvals ADD COLUMN actor_id TEXT",
        "ALTER TABLE publication_approvals ADD COLUMN nonce_sha256 CHAR(64)",
        "CONSTRAINT ck_publication_approvals_actor_id_nonempty "
        "CHECK (actor_id IS NOT NULL AND btrim(actor_id) <> '') NOT VALID",
        "CONSTRAINT ck_publication_approvals_nonce_sha256_lower_hex "
        "CHECK (nonce_sha256 IS NOT NULL AND nonce_sha256 ~ '^[0-9a-f]{64}$') "
        "NOT VALID",
        "CREATE FUNCTION reject_publication_approval_evidence_mutation()",
        "CREATE TRIGGER trg_publication_approvals_evidence_immutable "
        "BEFORE UPDATE ON publication_approvals",
        "OLD.actor_id IS DISTINCT FROM NEW.actor_id",
        "OLD.nonce_sha256 IS DISTINCT FROM NEW.nonce_sha256",
        "EXECUTE FUNCTION reject_publication_approval_evidence_mutation()",
        "legacy rows may remain NULL",
        "VALIDATE CONSTRAINT",
    )
    if migration.migration_id != "012":
        raise ValueError(
            "D11 publication approval metadata migration must use ID '012'"
        )
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D11 publication approval metadata migration checksum does not match "
            "its statements"
        )
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D11 publication approval metadata migration is missing a required "
            "schema constraint"
        )


validate_publication_approval_metadata_migration(
    PUBLICATION_APPROVAL_METADATA_MIGRATION
)


def validate_usage_ledger_schema_migration(migration: PostgresCatalogMigration) -> None:
    """Reject D12 definitions that lose scope or guess legacy job ownership."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "ALTER TABLE usage_events ADD COLUMN project_id UUID",
        "ALTER TABLE usage_events ADD COLUMN job_id UUID",
        "ALTER TABLE usage_events ADD COLUMN usage JSONB",
        "UPDATE usage_events SET project_id = publications.project_id",
        "episode_id = publications.episode_id",
        "HAVING count(*) = 1",
        "CONSTRAINT ck_usage_events_project_id CHECK (project_id IS NOT NULL) NOT VALID",
        "CONSTRAINT ck_usage_events_job_id CHECK (job_id IS NOT NULL) NOT VALID",
        "CONSTRAINT ck_usage_events_usage_object "
        "CHECK (usage IS NOT NULL AND jsonb_typeof(usage) = 'object') NOT VALID",
        "CONSTRAINT fk_usage_events_tenant_project",
        "CONSTRAINT fk_usage_events_tenant_job",
        "ALTER COLUMN publication_id DROP NOT NULL",
        "DROP CONSTRAINT ck_usage_events_costs",
        "ALTER COLUMN estimated_cost TYPE TEXT USING estimated_cost::TEXT",
        "ALTER COLUMN reconciled_cost TYPE TEXT USING reconciled_cost::TEXT",
        "ALTER TABLE usage_events DISABLE TRIGGER "
        "trg_usage_events_provenance_immutable",
        "ALTER TABLE usage_events ADD CONSTRAINT ck_usage_events_costs CHECK ("
        "CASE WHEN estimated_cost ~ '^(0|[1-9][0-9]*)(\\.[0-9]+)?$' "
        "THEN estimated_cost::NUMERIC >= 0 ELSE FALSE END AND "
        "CASE WHEN reconciled_cost IS NULL THEN TRUE WHEN reconciled_cost ~ "
        "'^(0|[1-9][0-9]*)(\\.[0-9]+)?$' THEN reconciled_cost::NUMERIC >= 0 "
        "ELSE FALSE END) NOT VALID",
        "CONSTRAINT uq_render_jobs_tenant_project_id "
        "UNIQUE (tenant_id, project_id, id)",
        "FOREIGN KEY (tenant_id, project_id, job_id) "
        "REFERENCES render_jobs (tenant_id, project_id, id) ON DELETE RESTRICT NOT VALID",
        "ALTER TABLE usage_events ENABLE TRIGGER trg_usage_events_provenance_immutable",
    )
    if migration.migration_id != "013":
        raise ValueError("D12 usage ledger migration must use ID '013'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D12 usage ledger migration checksum does not match its statements"
        )
    if any(fragment not in statements for fragment in required_fragments):
        raise ValueError(
            "D12 usage ledger migration is missing a required schema constraint"
        )


validate_usage_ledger_schema_migration(USAGE_LEDGER_SCHEMA_MIGRATION)


def validate_tenant_rls_schema_migration(migration: PostgresCatalogMigration) -> None:
    """Reject D13 definitions that allow absent context or owner bypasses."""
    statements = "\n".join(migration.statements)
    required_fragments = (
        "CREATE FUNCTION poddown_current_tenant_id() RETURNS UUID STABLE",
        "NULLIF(current_setting('app.tenant_id', true), '')::UUID",
        "SET LOCAL app.tenant_id",
        "SQLite local and test mode do not implement PostgreSQL RLS",
    )
    table_fragments = tuple(
        fragment
        for table in _TENANT_RLS_TABLES
        for fragment in (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL",
        )
    )
    policy_fragments = (
        "USING (tenant_id = poddown_current_tenant_id())",
        "WITH CHECK (tenant_id = poddown_current_tenant_id())",
    )
    if migration.migration_id != "014":
        raise ValueError("D13 tenant RLS migration must use ID '014'")
    if migration.checksum != _migration_checksum(migration.statements):
        raise ValueError(
            "D13 tenant RLS migration checksum does not match its statements"
        )
    if "BYPASSRLS" in statements or any(
        fragment not in statements
        for fragment in (*required_fragments, *table_fragments, *policy_fragments)
    ):
        raise ValueError("D13 tenant RLS migration is missing a required RLS safeguard")


validate_tenant_rls_schema_migration(TENANT_RLS_SCHEMA_MIGRATION)
