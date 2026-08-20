"""Opt-in pooled PostgreSQL proof for the D13 tenant transaction boundary.

This test exercises a live database and RLS policy; unit fakes only verify the
repository's transaction ordering and must not be read as RLS evidence.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from uuid6 import uuid7

from poddown.postgres_runtime import initialize_postgres
from poddown.postgres_schema import MIGRATIONS
from poddown.tenant_context import tenant_transaction

_POSTGRES_TEST_DSN = os.environ.get("PODDOWN_POSTGRES_TEST_DSN", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_DSN,
    reason="PostgreSQL RLS integration tests require PODDOWN_POSTGRES_TEST_DSN",
)


def test_pooled_postgres_connection_does_not_leak_tenant_rls_context() -> None:
    """A reused pooled connection cannot read a prior tenant's RLS-protected row."""
    psycopg_pool = pytest.importorskip(
        "psycopg_pool", reason="pooled PostgreSQL coverage requires psycopg-pool"
    )
    pool = psycopg_pool.ConnectionPool(_POSTGRES_TEST_DSN, min_size=1, max_size=1)
    table_name = f"poddown_d13_{uuid4().hex}"
    first_tenant, second_tenant = uuid7(), uuid7()
    try:
        with pool.connection() as connection:
            role = connection.execute(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            ).fetchone()
            if role is None or role[0]:
                pytest.skip("configured PostgreSQL role bypasses RLS")
            connection.execute(
                f"CREATE TABLE {table_name} "
                "(tenant_id UUID NOT NULL, value TEXT NOT NULL)"
            )
            connection.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
            connection.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
            connection.execute(
                f"CREATE POLICY tenant_isolation ON {table_name} FOR ALL "
                "USING (tenant_id = NULLIF(current_setting("
                "'app.tenant_id', true), '')::UUID) WITH CHECK (tenant_id = "
                "NULLIF(current_setting('app.tenant_id', true), '')::UUID)"
            )
            connection.commit()
            with tenant_transaction(connection, first_tenant) as cursor:
                cursor.execute(
                    f"INSERT INTO {table_name} (tenant_id, value) VALUES (%s, %s)",
                    (first_tenant, "first-tenant-only"),
                )
        with (
            pool.connection() as connection,
            tenant_transaction(connection, second_tenant) as cursor,
        ):
            cursor.execute(f"SELECT value FROM {table_name}")
            assert cursor.fetchall() == []
    finally:
        with pool.connection() as connection:
            connection.execute(f"DROP TABLE IF EXISTS {table_name}")
            connection.commit()
        pool.close()


def test_postgres_runtime_bootstraps_repository_tables_and_app_rls() -> None:
    """The runtime catalog must cover repositories and use the shared setting."""
    import psycopg

    admin = psycopg.connect(_POSTGRES_TEST_DSN, autocommit=False)
    schema = f"poddown_runtime_catalog_{uuid4().hex}"
    admin.execute(f'CREATE SCHEMA "{schema}"')
    admin.commit()

    def factory():
        connection = psycopg.connect(_POSTGRES_TEST_DSN, autocommit=False)
        connection.execute(f'SET search_path TO "{schema}"')
        connection.commit()
        return connection

    try:
        assert initialize_postgres(factory) == tuple(
            migration.version for migration in MIGRATIONS
        )
        probe = factory()
        try:
            role = probe.execute(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            ).fetchone()
            if role is None or role[0]:
                pytest.skip("configured PostgreSQL role bypasses RLS")
            tables = probe.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() "
                "AND table_name IN ('command_receipts', 'provider_evidence', "
                "'publication_receipts', 'object_references', 'object_inventory') "
                "ORDER BY table_name"
            ).fetchall()
            assert [row[0] for row in tables] == [
                "command_receipts",
                "object_inventory",
                "object_references",
                "provider_evidence",
                "publication_receipts",
            ]
            policies = probe.execute(
                "SELECT relation.relname, pg_get_expr(policy.polqual, policy.polrelid) "
                "FROM pg_policy AS policy "
                "JOIN pg_class AS relation ON relation.oid = policy.polrelid "
                "WHERE relation.relnamespace = current_schema()::regnamespace "
                "AND relation.relname IN ('command_receipts', 'object_inventory', "
                "'object_references', 'provider_evidence', 'publication_receipts') "
                "ORDER BY relation.relname"
            ).fetchall()
            assert len(policies) == 5
            assert all("app.tenant_id" in str(row[1]) for row in policies)
        finally:
            probe.close()
    finally:
        admin.rollback()
        admin.execute("RESET search_path")
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.commit()
        admin.close()
