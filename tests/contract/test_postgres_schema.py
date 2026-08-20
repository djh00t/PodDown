"""Contract coverage for the forward-only PostgreSQL tenancy schema."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

import poddown.postgres_schema as postgres_schema
from poddown.migrations import Migration
from poddown.postgres_schema import (
    AUDIO_MASTER_METADATA_DIGEST_MIGRATION,
    AUDIO_QA_SCHEMA_MIGRATION,
    CONTENT_SCHEMA_MIGRATION,
    EPISODE_SCHEMA_MIGRATION,
    EPISODE_SNAPSHOT_SCHEMA_MIGRATION,
    OUTBOX_LEASE_SCHEMA_MIGRATION,
    POSTGRESQL_MIGRATIONS,
    PUBLICATION_APPROVAL_METADATA_MIGRATION,
    PUBLISHING_METERING_SCHEMA_MIGRATION,
    RENDER_SCHEMA_MIGRATION,
    TENANCY_SCHEMA_MIGRATION,
    VOICE_PRONUNCIATION_SCHEMA_MIGRATION,
    validate_audio_master_metadata_digest_migration,
    validate_audio_qa_schema_migration,
    validate_content_schema_migration,
    validate_episode_schema_migration,
    validate_episode_snapshot_schema_migration,
    validate_outbox_lease_schema_migration,
    validate_publication_approval_metadata_migration,
    validate_publishing_metering_schema_migration,
    validate_render_schema_migration,
    validate_tenancy_schema_migration,
    validate_voice_pronunciation_schema_migration,
)


def test_episode_snapshot_migration_is_the_ordered_postgresql_catalog_entry() -> None:
    """D10 extends rather than rewrites the immutable production catalog."""
    migration = EPISODE_SNAPSHOT_SCHEMA_MIGRATION

    assert POSTGRESQL_MIGRATIONS[-6:-4] == (
        PUBLISHING_METERING_SCHEMA_MIGRATION,
        migration,
    )
    assert migration.migration_id == "011"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    validate_episode_snapshot_schema_migration(migration)


def test_publication_approval_metadata_migration_is_a_forward_only_successor() -> None:
    """D11 adds approval metadata without rewriting migrations 003 through 011."""
    migration = PUBLICATION_APPROVAL_METADATA_MIGRATION

    assert POSTGRESQL_MIGRATIONS[-4] == migration
    assert migration.migration_id == "012"
    assert tuple(item.migration_id for item in POSTGRESQL_MIGRATIONS[:-4]) == (
        "003",
        "004",
        "005",
        "006",
        "007",
        "008",
        "009",
        "010",
        "011",
    )
    assert all(
        item.checksum == sha256("\n".join(item.statements).encode()).hexdigest()
        for item in POSTGRESQL_MIGRATIONS[:-4]
    )
    validate_publication_approval_metadata_migration(migration)


def test_tenant_rls_migration_is_a_forward_only_successor() -> None:
    """D13 adds database-enforced isolation without rewriting D03 through D12."""
    migration = postgres_schema.TENANT_RLS_SCHEMA_MIGRATION

    assert POSTGRESQL_MIGRATIONS[-2] == migration
    assert migration.migration_id == "014"
    assert tuple(item.migration_id for item in POSTGRESQL_MIGRATIONS[:-2]) == (
        "003",
        "004",
        "005",
        "006",
        "007",
        "008",
        "009",
        "010",
        "011",
        "012",
        "013",
    )
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    postgres_schema.validate_tenant_rls_schema_migration(migration)


def test_tenant_rls_requires_explicit_context_and_forced_policies() -> None:
    """Missing tenant context and table ownership must never bypass isolation."""
    statements = "\n".join(postgres_schema.TENANT_RLS_SCHEMA_MIGRATION.statements)
    tenant_tables = (
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

    assert "current_setting('app.tenant_id', true)" in statements
    assert "NULLIF(current_setting('app.tenant_id', true), '')::UUID" in statements
    assert "BYPASSRLS" not in statements
    for table in tenant_tables:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in statements
        assert f"CREATE POLICY tenant_isolation ON {table}" in statements
        assert "USING (tenant_id = poddown_current_tenant_id())" in statements
        assert "WITH CHECK (tenant_id = poddown_current_tenant_id())" in statements


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param("FORCE ROW LEVEL SECURITY", id="force-rls"),
        pytest.param("current_setting('app.tenant_id', true)", id="tenant-context"),
        pytest.param(
            "WITH CHECK (tenant_id = poddown_current_tenant_id())", id="write-check"
        ),
    ],
)
def test_tenant_rls_validator_rejects_missing_fail_closed_safeguards(
    missing_fragment: str,
) -> None:
    """The D13 validator rejects policies that could expose or cross-write rows."""
    migration = postgres_schema.TENANT_RLS_SCHEMA_MIGRATION
    statements = tuple(
        statement.replace(missing_fragment, "") for statement in migration.statements
    )
    invalid_migration = replace(
        migration,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required RLS safeguard"):
        postgres_schema.validate_tenant_rls_schema_migration(invalid_migration)


def test_publication_approval_metadata_requires_legacy_safe_new_row_checks() -> None:
    """D11 requires actor and nonce evidence that direct SQL cannot rewrite."""
    statements = "\n".join(PUBLICATION_APPROVAL_METADATA_MIGRATION.statements)

    assert "ADD COLUMN actor_id TEXT" in statements
    assert "ADD COLUMN nonce_sha256 CHAR(64)" in statements
    assert (
        "CHECK (actor_id IS NOT NULL AND btrim(actor_id) <> '') NOT VALID" in statements
    )
    assert (
        "CHECK (nonce_sha256 IS NOT NULL AND nonce_sha256 ~ '^[0-9a-f]{64}$') "
        "NOT VALID" in statements
    )
    assert "legacy rows may remain NULL" in statements
    assert "VALIDATE CONSTRAINT" in statements
    assert (
        "CREATE FUNCTION reject_publication_approval_evidence_mutation()" in statements
    )
    assert "OLD.actor_id IS DISTINCT FROM NEW.actor_id" in statements
    assert "OLD.nonce_sha256 IS DISTINCT FROM NEW.nonce_sha256" in statements
    assert "BEFORE UPDATE ON publication_approvals" in statements


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param("ADD COLUMN actor_id TEXT", id="actor-column"),
        pytest.param("ADD COLUMN nonce_sha256 CHAR(64)", id="nonce-column"),
        pytest.param(
            "CHECK (actor_id IS NOT NULL AND btrim(actor_id) <> '') NOT VALID",
            id="actor-check",
        ),
        pytest.param(
            "CHECK (nonce_sha256 IS NOT NULL AND nonce_sha256 ~ '^[0-9a-f]{64}$') "
            "NOT VALID",
            id="nonce-check",
        ),
        pytest.param(
            "CREATE FUNCTION reject_publication_approval_evidence_mutation()",
            id="approval-evidence-immutability-function",
        ),
        pytest.param(
            "OLD.actor_id IS DISTINCT FROM NEW.actor_id",
            id="actor-immutability",
        ),
        pytest.param(
            "OLD.nonce_sha256 IS DISTINCT FROM NEW.nonce_sha256",
            id="nonce-immutability",
        ),
    ],
)
def test_publication_approval_metadata_rejects_missing_safeguards(
    missing_fragment: str,
) -> None:
    """The D11 validator rejects incomplete approval metadata migrations."""
    statements = tuple(
        statement.replace(missing_fragment, "")
        for statement in PUBLICATION_APPROVAL_METADATA_MIGRATION.statements
    )
    invalid_migration = replace(
        PUBLICATION_APPROVAL_METADATA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_publication_approval_metadata_migration(invalid_migration)


def test_episode_snapshot_migration_preserves_reconstructable_lifecycle_state() -> None:
    """D10 cannot lose the authoritative episode snapshot fields or safeguards."""
    statements = "\n".join(EPISODE_SNAPSHOT_SCHEMA_MIGRATION.statements)
    episode_statements = "\n".join(EPISODE_SCHEMA_MIGRATION.statements)

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
        "CHECK (source_sha256 IS NOT NULL AND source_sha256 ~ "
        "'^[0-9a-f]{64}$') NOT VALID",
        "CONSTRAINT ck_episodes_source_bytes_positive "
        "CHECK (source_bytes IS NOT NULL AND source_bytes > 0) NOT VALID",
        "CONSTRAINT ck_episodes_request_fingerprint "
        "CHECK (request_fingerprint IS NOT NULL AND request_fingerprint ~ "
        "'^[0-9a-f]{64}$') NOT VALID",
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
        "CHECK (failure_evidence IS NULL OR "
        "jsonb_typeof(failure_evidence) = 'object') NOT VALID",
        "CONSTRAINT ck_episodes_qa_passed_requires_evidence "
        "CHECK (state <> 'qa_passed' OR (qa_evidence IS NOT NULL AND "
        "jsonb_typeof(qa_evidence) = 'object')) NOT VALID",
        "CONSTRAINT ck_episodes_failed_requires_evidence "
        "CHECK (state <> 'failed' OR (failure_evidence IS NOT NULL AND "
        "jsonb_typeof(failure_evidence) = 'object')) NOT VALID",
        "CONSTRAINT ck_episodes_packaged_requires_checksum "
        "CHECK (state NOT IN ('packaged', 'published') OR "
        "package_sha256 IS NOT NULL) NOT VALID",
        "CONSTRAINT fk_episodes_tenant_source_sha256 "
        "FOREIGN KEY (tenant_id, source_sha256) "
        "REFERENCES source_documents (tenant_id, sha256) ON DELETE RESTRICT NOT VALID",
    )

    assert all(fragment in statements for fragment in required_fragments)
    assert (
        "CONSTRAINT uq_source_documents_tenant_sha256 UNIQUE (tenant_id, sha256)"
        in episode_statements
    )
    assert "tenant_id UUID NOT NULL" in episode_statements
    assert "project_id UUID NOT NULL" in episode_statements
    assert "version INTEGER NOT NULL DEFAULT 1" in episode_statements
    assert (
        "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP"
        in episode_statements
    )
    assert (
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP"
        in episode_statements
    )


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param(
            "CHECK (source_bytes IS NOT NULL AND source_bytes > 0) NOT VALID",
            id="positive-source-bytes",
        ),
        pytest.param(
            "CHECK (state IS NOT NULL AND state IN ("
            "'validated', 'scripted', 'rendered', "
            "'qa_passed', 'packaged', 'published', 'failed')) NOT VALID",
            id="allowed-episode-state",
        ),
        pytest.param(
            "CHECK (btrim(idempotency_key) <> '') NOT VALID",
            id="nonempty-idempotency-key",
        ),
        pytest.param(
            "CHECK (qa_evidence IS NULL OR "
            "jsonb_typeof(qa_evidence) = 'object') NOT VALID",
            id="qa-evidence-object",
        ),
        pytest.param(
            "CHECK (failure_evidence IS NULL OR "
            "jsonb_typeof(failure_evidence) = 'object') NOT VALID",
            id="failure-evidence-object",
        ),
        pytest.param(
            "CHECK (state <> 'qa_passed' OR (qa_evidence IS NOT NULL AND "
            "jsonb_typeof(qa_evidence) = 'object')) NOT VALID",
            id="qa-passed-evidence",
        ),
        pytest.param(
            "CHECK (state <> 'failed' OR (failure_evidence IS NOT NULL AND "
            "jsonb_typeof(failure_evidence) = 'object')) NOT VALID",
            id="failed-evidence",
        ),
        pytest.param(
            "CHECK (state NOT IN ('packaged', 'published') OR "
            "package_sha256 IS NOT NULL) NOT VALID",
            id="packaged-checksum",
        ),
        pytest.param(
            "CONSTRAINT fk_episodes_tenant_source_sha256 "
            "FOREIGN KEY (tenant_id, source_sha256) "
            "REFERENCES source_documents (tenant_id, sha256) "
            "ON DELETE RESTRICT NOT VALID",
            id="same-tenant-source",
        ),
    ],
)
def test_episode_snapshot_migration_validation_rejects_missing_lifecycle_safeguard(
    missing_fragment: str,
) -> None:
    """A D10 lifecycle safeguard cannot be omitted before migration application."""
    statements = tuple(
        statement.replace(missing_fragment, "")
        for statement in EPISODE_SNAPSHOT_SCHEMA_MIGRATION.statements
    )
    invalid_migration = replace(
        EPISODE_SNAPSHOT_SCHEMA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_episode_snapshot_schema_migration(invalid_migration)


def test_publishing_metering_migration_is_the_ordered_postgresql_catalog_entry() -> (
    None
):
    """D09 remains an immutable successor to the existing production schema."""
    migration = PUBLISHING_METERING_SCHEMA_MIGRATION

    assert POSTGRESQL_MIGRATIONS[-7:-5] == (
        AUDIO_MASTER_METADATA_DIGEST_MIGRATION,
        migration,
    )
    assert migration.migration_id == "010"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    validate_publishing_metering_schema_migration(migration)


def test_publishing_metering_migration_declares_scoped_durable_contracts() -> None:
    """D09 cannot lose publish authorization, metering, or delivery integrity."""
    statements = "\n".join(PUBLISHING_METERING_SCHEMA_MIGRATION.statements)

    required_fragments = (
        "CREATE TABLE publishing_targets",
        "CREATE TABLE publications",
        "CREATE TABLE publication_approvals",
        "CREATE TABLE usage_events",
        "CREATE TABLE provider_costs",
        "CREATE TABLE outbox_events",
        "CONSTRAINT ck_publishing_targets_id_uuidv7 "
        "CHECK (substring(id::text FROM 15 FOR 1) = '7')",
        "CONSTRAINT uq_publishing_targets_tenant_id UNIQUE (tenant_id, id)",
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
        "consumed_at TIMESTAMPTZ",
        "CONSTRAINT uq_publication_approvals_tenant_reference "
        "UNIQUE (tenant_id, approval_reference)",
        "CONSTRAINT fk_usage_events_tenant_publication "
        "FOREIGN KEY (tenant_id, publication_id) "
        "REFERENCES publications (tenant_id, id) ON DELETE RESTRICT",
        "CONSTRAINT uq_usage_events_tenant_provider_request "
        "UNIQUE (tenant_id, provider, provider_request_id)",
        "units NUMERIC(20,6) NOT NULL",
        "estimated_cost NUMERIC(20,6) NOT NULL",
        "reconciled_cost NUMERIC(20,6)",
        "CONSTRAINT fk_provider_costs_tenant_usage_event "
        "FOREIGN KEY (tenant_id, usage_event_id, provider, provider_request_id) "
        "REFERENCES usage_events (tenant_id, id, provider, provider_request_id) "
        "ON DELETE RESTRICT",
        "CONSTRAINT uq_provider_costs_tenant_provider_request "
        "UNIQUE (tenant_id, provider, provider_request_id, cost_kind)",
        "aggregate_version BIGINT NOT NULL",
        "payload JSONB NOT NULL",
        "publish_attempts INTEGER NOT NULL DEFAULT 0",
        "published_at TIMESTAMPTZ",
        "CONSTRAINT uq_outbox_events_tenant_aggregate_version "
        "UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version)",
        "CREATE TRIGGER trg_publishing_targets_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON publishing_targets",
        "CREATE TRIGGER trg_publication_approvals_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON publication_approvals",
        "CREATE TRIGGER trg_usage_events_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON usage_events",
        "CREATE TRIGGER trg_provider_costs_provenance_immutable "
        "BEFORE UPDATE OR DELETE ON provider_costs",
    )

    assert all(fragment in statements for fragment in required_fragments)
    assert "credential" not in statements.lower()
    assert statements.count("substring(id::text FROM 15 FOR 1) = '7'") == 6
    assert statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") == 12


def test_outbox_lease_migration_is_a_forward_only_trigger_safe_successor() -> None:
    """D16 must add lease state without changing applied migration 010."""
    assert tuple(item.migration_id for item in POSTGRESQL_MIGRATIONS[-2:]) == (
        "014",
        "015",
    )
    statements = "\n".join(POSTGRESQL_MIGRATIONS[-1].statements)

    assert "ALTER TABLE outbox_events ADD COLUMN claim_token UUID" in statements
    assert "ALTER TABLE outbox_events ADD COLUMN lease_until TIMESTAMPTZ" in statements
    assert (
        "DROP TRIGGER trg_outbox_events_provenance_immutable ON outbox_events"
        in statements
    )
    assert "CREATE TRIGGER trg_outbox_events_provenance_immutable " in statements
    assert "OLD.payload, OLD.created_at" in statements
    assert (
        "OLD.lease_until > CURRENT_TIMESTAMP AND NEW.claim_token IS NULL" in statements
    )
    validate_outbox_lease_schema_migration(OUTBOX_LEASE_SCHEMA_MIGRATION)


def test_outbox_lease_migration_validation_rejects_a_missing_live_lease_guard() -> None:
    """Removing the release guard would let expired workers defer dispatch."""
    statements = tuple(
        statement.replace("OLD.lease_until > CURRENT_TIMESTAMP AND ", "")
        for statement in OUTBOX_LEASE_SCHEMA_MIGRATION.statements
    )
    invalid_migration = replace(
        OUTBOX_LEASE_SCHEMA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_outbox_lease_schema_migration(invalid_migration)


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param(
            "CONSTRAINT uq_publications_tenant_idempotency_key "
            "UNIQUE (tenant_id, idempotency_key)",
            id="publication-idempotency",
        ),
        pytest.param(
            "CONSTRAINT ck_publication_approvals_expiry "
            "CHECK (expires_at > approved_at)",
            id="approval-expiry",
        ),
        pytest.param(
            "CONSTRAINT uq_publication_approvals_tenant_reference "
            "UNIQUE (tenant_id, approval_reference)",
            id="approval-reference-idempotency",
        ),
        pytest.param(
            "CONSTRAINT uq_usage_events_tenant_provider_request "
            "UNIQUE (tenant_id, provider, provider_request_id)",
            id="provider-request-idempotency",
        ),
        pytest.param(
            "CONSTRAINT uq_outbox_events_tenant_aggregate_version "
            "UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version)",
            id="outbox-aggregate-version",
        ),
        pytest.param(
            "CONSTRAINT fk_render_candidates_tenant_cost_reference "
            "FOREIGN KEY (tenant_id, cost_reference) "
            "REFERENCES provider_costs (tenant_id, id) ON DELETE RESTRICT",
            id="render-candidate-cost-reference",
        ),
    ],
)
def test_publishing_metering_migration_validation_rejects_missing_safeguard(
    missing_fragment: str,
) -> None:
    """A D09 integrity safeguard cannot be omitted before the runner records it."""
    statements = tuple(
        statement.replace(missing_fragment, "")
        for statement in PUBLISHING_METERING_SCHEMA_MIGRATION.statements
    )
    invalid_migration = replace(
        PUBLISHING_METERING_SCHEMA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_publishing_metering_schema_migration(invalid_migration)


def test_tenancy_migration_is_the_ordered_postgresql_catalog_entry() -> None:
    """The D03 schema stays an immutable, forward-only runner input."""
    migration = TENANCY_SCHEMA_MIGRATION

    assert POSTGRESQL_MIGRATIONS[0] == migration
    assert migration.migration_id == "003"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    validate_tenancy_schema_migration(migration)


def test_tenancy_migration_declares_tenant_scoped_uuidv7_schema() -> None:
    """Tenant isolation breaks if a table key, scope, or timestamp constraint drifts."""
    statements = "\n".join(TENANCY_SCHEMA_MIGRATION.statements)

    assert "CREATE TABLE tenants" in statements
    assert "CREATE TABLE projects" in statements
    assert "id UUID CONSTRAINT pk_tenants PRIMARY KEY" in statements
    assert "id UUID CONSTRAINT pk_projects PRIMARY KEY" in statements
    assert "substring(id::text FROM 15 FOR 1) = '7'" in statements
    assert "CONSTRAINT uq_tenants_slug UNIQUE (slug)" in statements
    assert "tenant_id UUID NOT NULL" in statements
    assert (
        "CONSTRAINT fk_projects_tenant FOREIGN KEY (tenant_id) "
        "REFERENCES tenants (id) ON DELETE RESTRICT"
    ) in statements
    assert "CONSTRAINT uq_projects_tenant_slug UNIQUE (tenant_id, slug)" in statements
    assert "CREATE INDEX ix_projects_tenant_id ON projects (tenant_id)" in statements
    assert statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") == 4


@pytest.mark.parametrize(
    "invalid_migration",
    [
        replace(TENANCY_SCHEMA_MIGRATION, migration_id="004"),
        replace(
            TENANCY_SCHEMA_MIGRATION,
            statements=("CREATE TABLE tenants (id UUID PRIMARY KEY)",),
        ),
    ],
)
def test_tenancy_migration_validation_rejects_wrong_identity_or_constraints(
    invalid_migration: Migration,
) -> None:
    """Malformed D03 definitions fail before a runner can record them."""
    with pytest.raises(ValueError):
        validate_tenancy_schema_migration(invalid_migration)


def test_episode_migration_is_the_ordered_postgresql_catalog_entry() -> None:
    """D04 remains an immutable, forward-only successor to the tenancy schema."""
    migration = EPISODE_SCHEMA_MIGRATION

    assert (TENANCY_SCHEMA_MIGRATION, migration) == POSTGRESQL_MIGRATIONS[:2]
    assert migration.migration_id == "004"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    validate_episode_schema_migration(migration)


def test_episode_migration_declares_tenant_scoped_immutable_source_versions() -> None:
    """Episode state cannot lose source fidelity, immutability, or version state."""
    statements = "\n".join(EPISODE_SCHEMA_MIGRATION.statements)

    required_fragments = (
        "CREATE TABLE source_documents",
        "CREATE TABLE episodes",
        "CREATE TABLE episode_versions",
        "CONSTRAINT uq_source_documents_tenant_sha256 UNIQUE (tenant_id, sha256)",
        "CONSTRAINT uq_source_documents_tenant_id_sha256 "
        "UNIQUE (tenant_id, id, sha256)",
        "CONSTRAINT ck_source_documents_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$')",
        "CONSTRAINT uq_episodes_tenant_idempotency_key "
        "UNIQUE (tenant_id, idempotency_key)",
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
        "CREATE FUNCTION reject_source_documents_mutation()",
        "CREATE TRIGGER trg_source_documents_immutable "
        "BEFORE UPDATE OR DELETE ON source_documents",
        "CREATE FUNCTION reject_episode_versions_mutation()",
        "CREATE TRIGGER trg_episode_versions_immutable "
        "BEFORE UPDATE OR DELETE ON episode_versions",
        "CREATE INDEX ix_episodes_tenant_project_id "
        "ON episodes (tenant_id, project_id)",
        "CREATE INDEX ix_episode_versions_tenant_episode_id "
        "ON episode_versions (tenant_id, episode_id)",
    )
    assert all(fragment in statements for fragment in required_fragments)
    assert statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") == 6


@pytest.mark.parametrize(
    "invalid_migration",
    [
        replace(EPISODE_SCHEMA_MIGRATION, migration_id="005"),
        replace(
            EPISODE_SCHEMA_MIGRATION,
            statements=("CREATE TABLE episodes (id UUID PRIMARY KEY)",),
        ),
    ],
)
def test_episode_migration_validation_rejects_missing_scope_or_version_constraints(
    invalid_migration: Migration,
) -> None:
    """Malformed D04 definitions fail before a runner can record them."""
    with pytest.raises(ValueError):
        validate_episode_schema_migration(invalid_migration)


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param(
            "CONSTRAINT fk_episode_versions_tenant_source_document_hash "
            "FOREIGN KEY (tenant_id, source_document_id, source_sha256) "
            "REFERENCES source_documents (tenant_id, id, sha256) ON DELETE RESTRICT",
            id="source-hash-reference",
        ),
        pytest.param(
            "CREATE TRIGGER trg_source_documents_immutable "
            "BEFORE UPDATE OR DELETE ON source_documents",
            id="source-document-immutability-trigger",
        ),
        pytest.param(
            "CONSTRAINT uq_episode_versions_episode_version "
            "UNIQUE (episode_id, version)",
            id="episode-version-uniqueness",
        ),
    ],
)
def test_episode_migration_validation_rejects_missing_required_constraint(
    missing_fragment: str,
) -> None:
    """A missing D04 integrity safeguard is rejected before migration application."""
    statements = tuple(
        statement.replace(missing_fragment, "")
        for statement in EPISODE_SCHEMA_MIGRATION.statements
    )
    invalid_migration = replace(
        EPISODE_SCHEMA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_episode_schema_migration(invalid_migration)


def test_content_migration_is_the_ordered_postgresql_catalog_entry() -> None:
    """D05 remains an immutable successor to the D03 and D04 migrations."""
    migration = CONTENT_SCHEMA_MIGRATION

    assert (
        TENANCY_SCHEMA_MIGRATION,
        EPISODE_SCHEMA_MIGRATION,
        migration,
    ) == POSTGRESQL_MIGRATIONS[:3]
    assert migration.migration_id == "005"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    validate_content_schema_migration(migration)


def test_content_migration_declares_tenant_scoped_immutable_content_relationships() -> (
    None
):
    """Content snapshots must retain exact script/profile identity and scope."""
    statements = "\n".join(CONTENT_SCHEMA_MIGRATION.statements)

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

    assert all(fragment in statements for fragment in required_fragments)
    assert statements.count("substring(id::text FROM 15 FOR 1) = '7'") == 4
    assert statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") == 8


def test_content_migration_keeps_scripts_independently_insertable() -> None:
    """The content hash edge is one-way to avoid an unsatisfiable FK cycle."""
    statements = "\n".join(CONTENT_SCHEMA_MIGRATION.statements)
    scripts_definition = next(
        statement
        for statement in CONTENT_SCHEMA_MIGRATION.statements
        if statement.startswith("CREATE TABLE scripts")
    )

    assert "tenant_id UUID NOT NULL" in scripts_definition
    assert "sha256 CHAR(64) NOT NULL" in scripts_definition
    assert "CONSTRAINT uq_scripts_tenant_sha256 UNIQUE (tenant_id, sha256)" in (
        scripts_definition
    )
    assert "episode_version_id" not in scripts_definition
    assert "REFERENCES episode_versions" not in scripts_definition
    assert "fk_scripts_tenant_episode_version" not in scripts_definition
    assert (
        "ALTER TABLE episode_versions ADD CONSTRAINT "
        "fk_episode_versions_tenant_script_hash "
        "FOREIGN KEY (tenant_id, script_sha256) "
        "REFERENCES scripts (tenant_id, sha256) ON DELETE RESTRICT"
    ) in statements


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param(
            "CONSTRAINT fk_script_versions_tenant_script_hash "
            "FOREIGN KEY (tenant_id, script_id, script_sha256) "
            "REFERENCES scripts (tenant_id, id, sha256) ON DELETE RESTRICT",
            id="script-content-hash-reference",
        ),
        pytest.param(
            "CONSTRAINT fk_speakers_tenant_profile "
            "FOREIGN KEY (tenant_id, profile_id) REFERENCES profiles (tenant_id, id) "
            "ON DELETE RESTRICT",
            id="tenant-scoped-speaker-profile-reference",
        ),
        pytest.param(
            "CREATE TRIGGER trg_script_versions_immutable "
            "BEFORE UPDATE OR DELETE ON script_versions",
            id="script-version-immutability-trigger",
        ),
    ],
)
def test_content_migration_validation_rejects_missing_required_constraint(
    missing_fragment: str,
) -> None:
    """A missing D05 integrity safeguard fails before migration application."""
    statements = tuple(
        statement.replace(missing_fragment, "")
        for statement in CONTENT_SCHEMA_MIGRATION.statements
    )
    invalid_migration = replace(
        CONTENT_SCHEMA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_content_schema_migration(invalid_migration)


def test_voice_pronunciation_migration_is_the_ordered_postgresql_catalog_entry() -> (
    None
):
    """D06 remains the immutable successor to D03-D05 schema migrations."""
    migration = VOICE_PRONUNCIATION_SCHEMA_MIGRATION

    assert (
        TENANCY_SCHEMA_MIGRATION,
        EPISODE_SCHEMA_MIGRATION,
        CONTENT_SCHEMA_MIGRATION,
        migration,
    ) == POSTGRESQL_MIGRATIONS[:4]
    assert migration.migration_id == "006"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    validate_voice_pronunciation_schema_migration(migration)


def test_voice_pronunciation_migration_declares_tenant_contracts() -> None:
    """Voice rights and pronunciation resolution retain their durable invariants."""
    statements = "\n".join(VOICE_PRONUNCIATION_SCHEMA_MIGRATION.statements)

    required_fragments = (
        "CREATE TABLE voice_assets",
        "CONSTRAINT ck_voice_assets_provider "
        "CHECK (provider IN ('local', 'elevenlabs', 'openai'))",
        "CREATE TABLE voice_profiles",
        "ALTER TABLE speakers ADD COLUMN voice_profile_id UUID",
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
        "CONSTRAINT fk_voice_consents_tenant_voice_asset "
        "FOREIGN KEY (tenant_id, voice_asset_id) "
        "REFERENCES voice_assets (tenant_id, id) ON DELETE RESTRICT",
        "CONSTRAINT fk_speakers_tenant_voice_profile "
        "FOREIGN KEY (tenant_id, voice_profile_id) "
        "REFERENCES voice_profiles (tenant_id, id) ON DELETE RESTRICT",
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
    )

    assert all(fragment in statements for fragment in required_fragments)
    assert statements.count("substring(id::text FROM 15 FOR 1) = '7'") == 5
    assert statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") == 10


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param(
            "CONSTRAINT ck_voice_assets_provider "
            "CHECK (provider IN ('local', 'elevenlabs', 'openai'))",
            id="voice-asset-provider-allowlist",
        ),
        pytest.param(
            "CONSTRAINT ck_voice_consents_allowed_providers "
            "CHECK (cardinality(allowed_providers) > 0 "
            "AND allowed_providers <@ ARRAY['local', 'elevenlabs', 'openai']::TEXT[])",
            id="voice-consent-provider-allowlist",
        ),
        pytest.param(
            "CONSTRAINT ck_voice_consents_validity_window "
            "CHECK (valid_until > valid_from)",
            id="consent-validity-window",
        ),
        pytest.param(
            "CONSTRAINT fk_speakers_tenant_voice_profile "
            "FOREIGN KEY (tenant_id, voice_profile_id) "
            "REFERENCES voice_profiles (tenant_id, id) ON DELETE RESTRICT",
            id="speaker-voice-profile-reference",
        ),
        pytest.param(
            "CONSTRAINT uq_pronunciation_entries_lexicon_normalized_key "
            "UNIQUE (lexicon_id, normalized_key)",
            id="normalized-pronunciation-key",
        ),
    ],
)
def test_voice_pronunciation_migration_validation_rejects_missing_required_constraint(
    missing_fragment: str,
) -> None:
    """A missing D06 integrity safeguard fails before migration application."""
    statements = tuple(
        statement.replace(missing_fragment, "")
        for statement in VOICE_PRONUNCIATION_SCHEMA_MIGRATION.statements
    )
    invalid_migration = replace(
        VOICE_PRONUNCIATION_SCHEMA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_voice_pronunciation_schema_migration(invalid_migration)


def test_render_migration_is_the_ordered_postgresql_catalog_entry() -> None:
    """D07 remains the immutable successor to the D03-D06 schema migrations."""
    migration = RENDER_SCHEMA_MIGRATION

    assert POSTGRESQL_MIGRATIONS[4] == migration
    assert migration.migration_id == "007"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    validate_render_schema_migration(migration)


def test_render_migration_declares_scoped_immutable_workflow_provenance() -> None:
    """Render evidence must remain tenant scoped and replay-safe."""
    statements = "\n".join(RENDER_SCHEMA_MIGRATION.statements)

    required_fragments = (
        "ALTER TABLE episodes ADD CONSTRAINT uq_episodes_tenant_id_project_id "
        "UNIQUE (tenant_id, id, project_id)",
        "ALTER TABLE episode_versions ADD CONSTRAINT "
        "uq_episode_versions_tenant_id_episode_id_script_sha256 "
        "UNIQUE (tenant_id, id, episode_id, script_sha256)",
        "CREATE TABLE render_jobs",
        "CREATE TABLE render_segments",
        "CREATE TABLE render_candidates",
        "workflow_id TEXT NOT NULL",
        "CONSTRAINT uq_render_jobs_tenant_workflow_id UNIQUE (tenant_id, workflow_id)",
        "FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ",
        "FOREIGN KEY (tenant_id, episode_id) REFERENCES episodes (tenant_id, id) ",
        "FOREIGN KEY (tenant_id, episode_id, project_id) ",
        "REFERENCES episodes (tenant_id, id, project_id) ",
        "FOREIGN KEY (tenant_id, episode_version_id, episode_id, content_sha256) ",
        "REFERENCES episode_versions (tenant_id, id, episode_id, script_sha256) ",
        "CONSTRAINT ck_render_jobs_status ",
        "CONSTRAINT ck_render_jobs_attempt CHECK (attempt > 0)",
        "CONSTRAINT uq_render_segments_job_segment_attempt ",
        "UNIQUE (render_job_id, segment_id, attempt)",
        "CONSTRAINT uq_render_segments_tenant_id_job UNIQUE "
        "(tenant_id, id, render_job_id)",
        "CONSTRAINT ck_render_segments_status ",
        "CONSTRAINT uq_render_candidates_segment_take ",
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
        "CONSTRAINT uq_render_candidates_tenant_provider_request ",
        "UNIQUE (tenant_id, provider, provider_request_id)",
        "CREATE INDEX ix_render_jobs_tenant_project_episode_version_id ",
        "CREATE INDEX ix_render_segments_tenant_job_id ",
        "CREATE INDEX ix_render_candidates_tenant_segment_id ",
    )

    assert all(fragment in statements for fragment in required_fragments)
    assert statements.count("substring(id::text FROM 15 FOR 1) = '7'") == 3
    assert statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") == 6


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param(
            "CONSTRAINT uq_render_jobs_tenant_workflow_id UNIQUE "
            "(tenant_id, workflow_id)",
            id="workflow-identity",
        ),
        pytest.param(
            "FOREIGN KEY (tenant_id, episode_version_id, episode_id, content_sha256) ",
            id="content-snapshot-binding",
        ),
        pytest.param(
            "FOREIGN KEY (tenant_id, episode_id, project_id) ",
            id="episode-project-binding",
        ),
        pytest.param(
            "CONSTRAINT uq_render_candidates_segment_take ",
            id="candidate-take-identity",
        ),
        pytest.param(
            "CONSTRAINT uq_render_candidates_tenant_provider_request ",
            id="provider-request-identity",
        ),
    ],
)
def test_render_migration_validation_rejects_missing_required_constraint(
    missing_fragment: str,
) -> None:
    """A missing D07 workflow safeguard fails before migration application."""
    statements = tuple(
        statement.replace(missing_fragment, "")
        for statement in RENDER_SCHEMA_MIGRATION.statements
    )
    invalid_migration = replace(
        RENDER_SCHEMA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_render_schema_migration(invalid_migration)


def test_audio_qa_migration_is_the_ordered_postgresql_catalog_entry() -> None:
    """D08 follows the render migration with immutable QA and master evidence."""
    migration = AUDIO_QA_SCHEMA_MIGRATION

    assert POSTGRESQL_MIGRATIONS[5] == migration
    assert migration.migration_id == "008"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    validate_audio_qa_schema_migration(migration)


def test_audio_master_metadata_digest_correction_is_appended_to_d08() -> None:
    """A master metadata digest cannot differ from canonical JSONB metadata."""
    migration = AUDIO_MASTER_METADATA_DIGEST_MIGRATION

    assert POSTGRESQL_MIGRATIONS[5:7] == (
        AUDIO_QA_SCHEMA_MIGRATION,
        AUDIO_MASTER_METADATA_DIGEST_MIGRATION,
    )
    assert migration.migration_id == "009"
    assert migration.statements == (
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "ALTER TABLE audio_masters ADD CONSTRAINT "
        "ck_audio_masters_metadata_sha256_matches_metadata "
        "CHECK (mastering_metadata_sha256 = "
        "encode(digest(mastering_metadata::text, 'sha256'), 'hex'))",
    )
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )


def test_audio_master_metadata_digest_validation_rejects_missing_digest_check() -> None:
    """Removing the canonical metadata digest check invalidates migration 009."""
    digest_check = (
        "CHECK (mastering_metadata_sha256 = "
        "encode(digest(mastering_metadata::text, 'sha256'), 'hex'))"
    )
    statements = tuple(
        statement.replace(digest_check, "")
        for statement in AUDIO_MASTER_METADATA_DIGEST_MIGRATION.statements
    )
    invalid_migration = replace(
        AUDIO_MASTER_METADATA_DIGEST_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_audio_master_metadata_digest_migration(invalid_migration)


def test_audio_qa_migration_binds_transcripts_qa_and_masters_to_provenance() -> None:
    """Transcript, QA, and master records cannot detach from their source evidence."""
    statements = "\n".join(AUDIO_QA_SCHEMA_MIGRATION.statements)

    required_fragments = (
        "CREATE TABLE transcripts",
        "CREATE TABLE qa_results",
        "CREATE TABLE audio_masters",
        "CONSTRAINT uq_render_candidates_tenant_id_response_audio ",
        "UNIQUE (tenant_id, id, response_sha256, audio_sha256)",
        "CONSTRAINT uq_episode_versions_tenant_id_source_sha256 ",
        "UNIQUE (tenant_id, id, source_sha256)",
        "render_response_sha256 CHAR(64) NOT NULL",
        "CONSTRAINT ck_transcripts_evidence_kind ",
        "CHECK (evidence_kind IN ('provider-asr', 'script-derived'))",
        "CONSTRAINT fk_transcripts_tenant_candidate_provenance ",
        "FOREIGN KEY (tenant_id, render_candidate_id, render_response_sha256, "
        "audio_sha256) ",
        "REFERENCES render_candidates (tenant_id, id, response_sha256, audio_sha256) ",
        "CONSTRAINT fk_qa_results_tenant_transcript_audio ",
        "FOREIGN KEY (tenant_id, transcript_id, audio_sha256) ",
        "REFERENCES transcripts (tenant_id, id, audio_sha256)",
        "critical_token_accuracy NUMERIC(5,4) NOT NULL",
        "CONSTRAINT ck_qa_results_pass_requires_perfect_critical_tokens ",
        "CHECK (gate_status <> 'pass' OR critical_token_accuracy = 1.0000)",
        "mastering_metadata JSONB NOT NULL",
        "mastering_metadata_sha256 CHAR(64) NOT NULL",
        "CONSTRAINT fk_audio_masters_tenant_episode_version_source ",
        "FOREIGN KEY (tenant_id, episode_version_id, source_sha256) ",
        "REFERENCES episode_versions (tenant_id, id, source_sha256)",
        "CREATE TRIGGER trg_transcripts_provenance_immutable",
        "CREATE TRIGGER trg_qa_results_provenance_immutable",
        "CREATE TRIGGER trg_audio_masters_provenance_immutable",
    )

    assert all(fragment in statements for fragment in required_fragments)
    assert statements.count("substring(id::text FROM 15 FOR 1) = '7'") == 3
    assert statements.count("TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP") == 6


@pytest.mark.parametrize(
    "missing_fragment",
    [
        pytest.param(
            "FOREIGN KEY (tenant_id, render_candidate_id, render_response_sha256, "
            "audio_sha256) ",
            id="transcript-render-audio-binding",
        ),
        pytest.param(
            "CHECK (evidence_kind IN ('provider-asr', 'script-derived'))",
            id="transcript-evidence-kind",
        ),
        pytest.param(
            "CHECK (gate_status <> 'pass' OR critical_token_accuracy = 1.0000)",
            id="qa-critical-token-gate",
        ),
        pytest.param(
            "FOREIGN KEY (tenant_id, episode_version_id, source_sha256) ",
            id="master-source-binding",
        ),
        pytest.param(
            "CREATE TRIGGER trg_audio_masters_provenance_immutable ",
            id="master-provenance-immutability",
        ),
    ],
)
def test_audio_qa_migration_validation_rejects_missing_required_constraint(
    missing_fragment: str,
) -> None:
    """A missing D08 evidence safeguard fails before migration application."""
    statements = tuple(
        statement.replace(missing_fragment, "")
        for statement in AUDIO_QA_SCHEMA_MIGRATION.statements
    )
    invalid_migration = replace(
        AUDIO_QA_SCHEMA_MIGRATION,
        checksum=sha256("\n".join(statements).encode()).hexdigest(),
        statements=statements,
    )

    with pytest.raises(ValueError, match="required schema constraint"):
        validate_audio_qa_schema_migration(invalid_migration)
