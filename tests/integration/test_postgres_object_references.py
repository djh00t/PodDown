"""Opt-in PostgreSQL evidence for scoped object references and orphan cleanup."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from uuid6 import uuid7

from poddown.object_storage import ObjectRef
from poddown.postgres_objects import (
    ObjectReferenceNotFound,
    OrphanCleanupService,
    PostgresObjectInventoryRepository,
    PostgresObjectReferenceRepository,
)
from poddown.postgres_schema import MigrationRunner

_POSTGRES_TEST_DSN = os.environ.get("PODDOWN_POSTGRES_TEST_DSN", "").strip()

if _POSTGRES_TEST_DSN:
    import psycopg

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_DSN,
    reason="PostgreSQL runtime contract tests require PODDOWN_POSTGRES_TEST_DSN",
)

TENANT_A = UUID("0198d59c-b420-70ce-ae46-786eeeb387e1")
TENANT_B = UUID("0198d59c-b420-70ce-ae46-786eeeb387e2")
PROJECT_A = UUID("0198d59c-b420-70ce-ae46-786eeeb387e3")
PROJECT_B = UUID("0198d59c-b420-70ce-ae46-786eeeb387e4")
OBSERVED_AT = datetime(2026, 8, 12, tzinfo=UTC)
NOW = OBSERVED_AT + timedelta(days=2)


@pytest.fixture
def postgres_object_context() -> Iterator[
    tuple[
        Callable[[], psycopg.Connection],
        PostgresObjectReferenceRepository,
        PostgresObjectInventoryRepository,
    ]
]:
    """Provide isolated schema and tenant-scoped PostgreSQL repositories."""
    connection = psycopg.connect(_POSTGRES_TEST_DSN)
    schema = f"poddown_d18_{uuid7().hex}"
    connection.execute(f'CREATE SCHEMA "{schema}"')
    connection.execute(f'SET search_path TO "{schema}"')
    connection.commit()
    MigrationRunner().apply(connection)
    connection.execute(
        "INSERT INTO tenants (tenant_id) VALUES (%s), (%s)",
        (TENANT_A, TENANT_B),
    )
    connection.execute(
        "SELECT set_config('app.tenant_id', %s, false)",
        (str(TENANT_A),),
    )
    connection.execute(
        "INSERT INTO projects (tenant_id, project_id, name) VALUES (%s, %s, %s)",
        (TENANT_A, PROJECT_A, "d18-project-a"),
    )
    connection.execute(
        "SELECT set_config('app.tenant_id', %s, false)",
        (str(TENANT_B),),
    )
    connection.execute(
        "INSERT INTO projects (tenant_id, project_id, name) VALUES (%s, %s, %s)",
        (TENANT_B, PROJECT_B, "d18-project-b"),
    )
    connection.commit()

    def connect_repository() -> psycopg.Connection:
        return psycopg.connect(
            _POSTGRES_TEST_DSN,
            options=f'-c search_path="{schema}"',
        )

    references = PostgresObjectReferenceRepository(connect_repository)
    inventory = PostgresObjectInventoryRepository(connect_repository)
    try:
        yield connect_repository, references, inventory
    finally:
        connection.rollback()
        connection.execute("RESET search_path")
        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        connection.commit()
        connection.close()


def _reference() -> ObjectRef:
    """Create one canonical referenced object for tenant A."""
    data = b"d18 referenced object"
    digest = hashlib.sha256(data).hexdigest()
    return ObjectRef(
        tenant_id=TENANT_A,
        project_id=PROJECT_A,
        name="episode.mp3",
        media_type="audio/mpeg",
        byte_count=len(data),
        sha256=digest,
        storage_key=(
            f"tenants/{TENANT_A}/projects/{PROJECT_A}/objects/{digest[:2]}/{digest}"
        ),
    )


def test_postgres_object_references_and_orphan_cleanup_are_tenant_scoped(
    postgres_object_context: tuple[
        Callable[[], psycopg.Connection],
        PostgresObjectReferenceRepository,
        PostgresObjectInventoryRepository,
    ],
) -> None:
    """Join durable references to stale inventory without crossing tenants."""
    _connect, references, inventory = postgres_object_context
    reference = _reference()
    orphan_key = f"tenants/{TENANT_A}/projects/{PROJECT_A}/objects/orphan"

    assert references.record(reference) == reference
    inventory.observe(
        TENANT_A,
        PROJECT_A,
        (reference.storage_key, orphan_key),
        observed_at=OBSERVED_AT,
    )
    inventory.observe(
        TENANT_B,
        PROJECT_B,
        (orphan_key,),
        observed_at=OBSERVED_AT,
    )

    deleted: list[str] = []
    cleanup = OrphanCleanupService(references=references, inventory=inventory)
    report = cleanup.collect_project(
        TENANT_A,
        PROJECT_A,
        (reference.storage_key, orphan_key),
        now=NOW,
        grace_period=timedelta(days=1),
        delete=deleted.append,
        observed_at=NOW,
    )

    assert report.deleted_keys == (orphan_key,)
    assert report.retained_keys == (reference.storage_key,)
    assert deleted == [orphan_key]
    assert inventory.list_for_project(TENANT_A, PROJECT_A) == (
        type(inventory.list_for_project(TENANT_A, PROJECT_A)[0])(
            reference.storage_key,
            OBSERVED_AT,
        ),
    )
    assert inventory.list_for_project(TENANT_B, PROJECT_B)[0].storage_key == orphan_key
    with pytest.raises(ObjectReferenceNotFound):
        references.get(TENANT_B, PROJECT_B, reference.sha256)
