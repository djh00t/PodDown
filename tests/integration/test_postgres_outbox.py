"""Opt-in PostgreSQL coverage for the D16 outbox lease trigger."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from uuid6 import uuid7

from poddown.migrations import run_migrations
from poddown.postgres_schema import POSTGRESQL_MIGRATIONS

_POSTGRES_TEST_DSN = os.environ.get("PODDOWN_POSTGRES_TEST_DSN", "").strip()

if TYPE_CHECKING:
    import psycopg

if _POSTGRES_TEST_DSN:
    import psycopg
    from psycopg.errors import RaiseException

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_DSN,
    reason="PostgreSQL runtime contract tests require PODDOWN_POSTGRES_TEST_DSN",
)


@pytest.fixture
def postgres_connection() -> Iterator[psycopg.Connection]:
    """Provide an isolated schema in the explicitly configured PostgreSQL database."""
    connection = psycopg.connect(_POSTGRES_TEST_DSN)
    schema = f"poddown_d16_{uuid4().hex}"
    connection.execute(f'CREATE SCHEMA "{schema}"')
    connection.execute(f'SET search_path TO "{schema}"')
    connection.commit()
    try:
        yield connection
    finally:
        connection.rollback()
        connection.execute("RESET search_path")
        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        connection.commit()
        connection.close()


def test_postgres_d16_allows_retry_release_and_rejects_payload_mutation(
    postgres_connection: psycopg.Connection,
) -> None:
    """D16 permits live release state but leaves durable event evidence immutable."""
    connection = postgres_connection
    tenant_id = uuid7()
    event_id = uuid7()
    aggregate_id = uuid7()
    claim_token = uuid4()
    run_migrations(connection, POSTGRESQL_MIGRATIONS)

    with connection.transaction():
        connection.execute(
            "INSERT INTO tenants (id, slug) VALUES (%s, %s)", (tenant_id, "d16")
        )
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),)
        )
        connection.execute(
            "INSERT INTO outbox_events (id, tenant_id, aggregate_type, aggregate_id, "
            "aggregate_version, event_type, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (event_id, tenant_id, "episode", aggregate_id, 1, "episode.created", "{}"),
        )
        connection.execute(
            "UPDATE outbox_events SET publish_attempts = 1, claim_token = %s, "
            "lease_until = CURRENT_TIMESTAMP + INTERVAL '5 minutes' WHERE id = %s",
            (claim_token, event_id),
        )
        connection.execute(
            "UPDATE outbox_events SET claim_token = NULL, lease_until = NULL, "
            "available_at = CURRENT_TIMESTAMP + INTERVAL '1 minute' "
            "WHERE id = %s AND claim_token = %s AND lease_until > CURRENT_TIMESTAMP",
            (event_id, claim_token),
        )

    with connection.transaction():
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),)
        )
        with pytest.raises(RaiseException, match="provenance is immutable"):
            connection.execute(
                "UPDATE outbox_events SET payload = %s WHERE id = %s",
                ('{"x":1}', event_id),
            )
