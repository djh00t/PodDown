"""Contract coverage for D12 tenant-scoped usage ledger schema."""

from hashlib import sha256

from poddown.postgres_schema import (
    POSTGRESQL_MIGRATIONS,
    USAGE_LEDGER_SCHEMA_MIGRATION,
    validate_usage_ledger_schema_migration,
)


def test_usage_ledger_migration_is_the_forward_only_successor_to_d11() -> None:
    """Catch D12 rewriting migration 012 or losing tenant/job ownership."""
    migration = USAGE_LEDGER_SCHEMA_MIGRATION
    statements = "\n".join(migration.statements)

    assert POSTGRESQL_MIGRATIONS[-3] == migration
    assert migration.migration_id == "013"
    assert tuple(item.migration_id for item in POSTGRESQL_MIGRATIONS[:-3])[-1] == "012"
    assert (
        migration.checksum
        == sha256("\n".join(migration.statements).encode()).hexdigest()
    )
    assert "ALTER TABLE usage_events ADD COLUMN project_id UUID" in statements
    assert "ALTER TABLE usage_events ADD COLUMN job_id UUID" in statements
    assert "UPDATE usage_events SET project_id = publications.project_id" in statements
    assert "episode_id = publications.episode_id" in statements
    assert "CONSTRAINT fk_usage_events_tenant_project" in statements
    assert "CONSTRAINT fk_usage_events_tenant_job" in statements
    assert "FOREIGN KEY (tenant_id, project_id, job_id)" in statements
    assert "REFERENCES render_jobs (tenant_id, project_id, id)" in statements
    assert "ALTER COLUMN publication_id DROP NOT NULL" in statements
    assert "ALTER COLUMN estimated_cost TYPE TEXT" in statements
    assert "ALTER COLUMN reconciled_cost TYPE TEXT" in statements
    assert "DROP CONSTRAINT ck_usage_events_costs" in statements
    assert "ADD CONSTRAINT ck_usage_events_costs" in statements
    assert "estimated_cost ~ '^(0|[1-9][0-9]*)(\\.[0-9]+)?$'" in statements
    assert "reconciled_cost ~ '^(0|[1-9][0-9]*)(\\.[0-9]+)?$'" in statements
    assert "usage JSONB" in statements
    validate_usage_ledger_schema_migration(migration)


def test_usage_ledger_backfill_disables_only_its_immutability_trigger() -> None:
    """Allow legacy normalization without leaving usage evidence mutable."""
    statements = USAGE_LEDGER_SCHEMA_MIGRATION.statements
    disable = (
        "ALTER TABLE usage_events DISABLE TRIGGER trg_usage_events_provenance_immutable"
    )
    enable = (
        "ALTER TABLE usage_events ENABLE TRIGGER trg_usage_events_provenance_immutable"
    )

    assert disable in statements
    assert enable in statements
    assert statements.index(disable) < statements.index(
        "UPDATE usage_events SET project_id = publications.project_id "
        "FROM publications WHERE usage_events.tenant_id = publications.tenant_id "
        "AND usage_events.publication_id = publications.id"
    )
    assert statements.index(enable) > statements.index(
        "UPDATE usage_events SET job_id = candidates.job_id FROM ("
        "SELECT publications.tenant_id, publications.id AS publication_id, "
        "(array_agg(render_jobs.id ORDER BY render_jobs.id))[1] AS job_id "
        "FROM publications JOIN render_jobs "
        "ON render_jobs.tenant_id = publications.tenant_id "
        "AND render_jobs.project_id = publications.project_id "
        "AND render_jobs.episode_id = publications.episode_id GROUP BY "
        "publications.tenant_id, publications.id HAVING count(*) = 1"
        ") AS candidates WHERE usage_events.tenant_id = candidates.tenant_id "
        "AND usage_events.publication_id = candidates.publication_id"
    )
