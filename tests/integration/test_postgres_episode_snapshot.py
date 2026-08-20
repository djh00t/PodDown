"""Opt-in PostgreSQL runtime coverage for the D10 episode snapshot contract."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from uuid6 import uuid7

from poddown.episode_service import EpisodeRecord, EpisodeState, StructuredFailure
from poddown.migrations import run_migrations
from poddown.postgres_schema import POSTGRESQL_MIGRATIONS

_POSTGRES_TEST_DSN = os.environ.get("PODDOWN_POSTGRES_TEST_DSN", "").strip()

if TYPE_CHECKING:
    import psycopg

if _POSTGRES_TEST_DSN:
    import psycopg
    from psycopg.errors import CheckViolation, ForeignKeyViolation
    from psycopg.types.json import Json

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_DSN,
    reason="PostgreSQL runtime contract tests require PODDOWN_POSTGRES_TEST_DSN",
)


@pytest.fixture
def postgres_connection() -> Iterator[psycopg.Connection]:
    """Provide an isolated schema in the explicitly configured PostgreSQL database."""
    connection = psycopg.connect(_POSTGRES_TEST_DSN)
    schema = f"poddown_d10_{uuid4().hex}"
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


def _assert_rejected(
    connection: psycopg.Connection,
    statement: str,
    parameters: tuple[object, ...],
    error_type: type[Exception],
) -> None:
    """Assert one database rejection while rolling back its failed transaction."""
    with pytest.raises(error_type), connection.transaction():
        connection.execute(statement, parameters)


def _insert_episode(
    connection: psycopg.Connection,
    *,
    episode_id: object,
    tenant_id: object,
    project_id: object,
    idempotency_key: str,
    profile_name: str,
    source_sha256: str,
    source_bytes: int,
    request_fingerprint: str,
    state: str,
    qa_evidence: object = None,
    package_sha256: str | None = None,
    failure_evidence: object = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> None:
    """Insert one episode snapshot through the schema's complete row shape."""
    if created_at is None:
        created_at = datetime(2026, 8, 12, tzinfo=UTC)
    if updated_at is None:
        updated_at = created_at
    connection.execute(
        "INSERT INTO episodes ("
        "id, tenant_id, project_id, idempotency_key, version, profile_name, "
        "source_sha256, source_bytes, request_fingerprint, state, qa_evidence, "
        "package_sha256, failure_evidence, created_at, updated_at"
        ") VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            episode_id,
            tenant_id,
            project_id,
            idempotency_key,
            profile_name,
            source_sha256,
            source_bytes,
            request_fingerprint,
            state,
            Json(qa_evidence) if qa_evidence is not None else None,
            package_sha256,
            Json(failure_evidence) if failure_evidence is not None else None,
            created_at,
            updated_at,
        ),
    )


def test_postgres_d10_preserves_legacy_rows_and_enforces_new_snapshots(
    postgres_connection: psycopg.Connection,
) -> None:
    """Migration 011 preserves legacy rows while enforcing complete new snapshots."""
    connection = postgres_connection
    tenant_a = uuid7()
    tenant_b = uuid7()
    project_a = uuid7()
    project_b = uuid7()
    source_a = uuid7()
    source_b = uuid7()
    legacy_episode = uuid7()
    source_a_sha256 = hashlib.sha256(b"source-a").hexdigest()
    source_b_sha256 = hashlib.sha256(b"source-b").hexdigest()

    run_migrations(connection, POSTGRESQL_MIGRATIONS[:2])
    with connection.transaction():
        connection.execute(
            "INSERT INTO tenants (id, slug) VALUES (%s, %s), (%s, %s)",
            (tenant_a, "d10-tenant-a", tenant_b, "d10-tenant-b"),
        )
        connection.execute(
            "INSERT INTO projects (id, tenant_id, slug) "
            "VALUES (%s, %s, %s), (%s, %s, %s)",
            (
                project_a,
                tenant_a,
                "d10-project-a",
                project_b,
                tenant_b,
                "d10-project-b",
            ),
        )
        connection.execute(
            "INSERT INTO source_documents (id, tenant_id, sha256, source_reference) "
            "VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)",
            (
                source_a,
                tenant_a,
                source_a_sha256,
                "d10-source-a",
                source_b,
                tenant_b,
                source_b_sha256,
                "d10-source-b",
            ),
        )
        connection.execute(
            "INSERT INTO episodes (id, tenant_id, project_id, idempotency_key, "
            "version) "
            "VALUES (%s, %s, %s, %s, %s)",
            (legacy_episode, tenant_a, project_a, "", 1),
        )

    run_migrations(connection, POSTGRESQL_MIGRATIONS)
    connection.execute(
        "SELECT set_config('app.tenant_id', %s, false)",
        (str(tenant_a),),
    )
    connection.commit()

    legacy = connection.execute(
        "SELECT idempotency_key, profile_name, source_sha256, source_bytes, "
        "request_fingerprint, state, qa_evidence, package_sha256, failure_evidence "
        "FROM episodes WHERE id = %s",
        (legacy_episode,),
    ).fetchone()
    assert legacy == ("", None, None, None, None, None, None, None, None)

    base_values = {
        "tenant_id": tenant_a,
        "project_id": project_a,
        "profile_name": "default",
        "source_sha256": source_a_sha256,
        "source_bytes": 10,
        "request_fingerprint": hashlib.sha256(b"complete-request").hexdigest(),
    }
    _assert_rejected(
        connection,
        "INSERT INTO episodes (id, tenant_id, project_id, idempotency_key, version, "
        "profile_name, source_sha256, source_bytes, request_fingerprint, state) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s)",
        (
            uuid7(),
            base_values["tenant_id"],
            base_values["project_id"],
            "missing-failure",
            base_values["profile_name"],
            base_values["source_sha256"],
            base_values["source_bytes"],
            base_values["request_fingerprint"],
            EpisodeState.FAILED.value,
        ),
        CheckViolation,
    )
    _assert_rejected(
        connection,
        "INSERT INTO episodes (id, tenant_id, project_id, idempotency_key, version, "
        "profile_name, source_sha256, source_bytes, request_fingerprint, state, "
        "qa_evidence) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s)",
        (
            uuid7(),
            base_values["tenant_id"],
            base_values["project_id"],
            "array-qa",
            base_values["profile_name"],
            base_values["source_sha256"],
            base_values["source_bytes"],
            base_values["request_fingerprint"],
            EpisodeState.VALIDATED.value,
            Json(["not-an-object"]),
        ),
        CheckViolation,
    )
    _assert_rejected(
        connection,
        "INSERT INTO episodes (id, tenant_id, project_id, idempotency_key, version, "
        "profile_name, source_sha256, source_bytes, request_fingerprint, state) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s)",
        (
            uuid7(),
            base_values["tenant_id"],
            base_values["project_id"],
            "missing-package",
            base_values["profile_name"],
            base_values["source_sha256"],
            base_values["source_bytes"],
            base_values["request_fingerprint"],
            EpisodeState.PACKAGED.value,
        ),
        CheckViolation,
    )
    _assert_rejected(
        connection,
        "INSERT INTO episodes (id, tenant_id, project_id, idempotency_key, version, "
        "profile_name, source_sha256, source_bytes, request_fingerprint, state, "
        "failure_evidence) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s)",
        (
            uuid7(),
            base_values["tenant_id"],
            base_values["project_id"],
            "scalar-failure",
            base_values["profile_name"],
            base_values["source_sha256"],
            base_values["source_bytes"],
            base_values["request_fingerprint"],
            EpisodeState.FAILED.value,
            Json("not-an-object"),
        ),
        CheckViolation,
    )
    _assert_rejected(
        connection,
        "INSERT INTO episodes (id, tenant_id, project_id, idempotency_key, version, "
        "profile_name, source_sha256, source_bytes, request_fingerprint, state) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s)",
        (
            uuid7(),
            base_values["tenant_id"],
            base_values["project_id"],
            "cross-tenant-source",
            base_values["profile_name"],
            source_b_sha256,
            base_values["source_bytes"],
            base_values["request_fingerprint"],
            EpisodeState.VALIDATED.value,
        ),
        ForeignKeyViolation,
    )
    _assert_rejected(
        connection,
        "INSERT INTO episodes (id, tenant_id, project_id, idempotency_key, version, "
        "profile_name, source_sha256, source_bytes, request_fingerprint, state) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s)",
        (
            uuid7(),
            base_values["tenant_id"],
            base_values["project_id"],
            "missing-source",
            base_values["profile_name"],
            "f" * 64,
            base_values["source_bytes"],
            base_values["request_fingerprint"],
            EpisodeState.VALIDATED.value,
        ),
        ForeignKeyViolation,
    )

    mutable_episode = uuid7()
    _insert_episode(
        connection,
        episode_id=mutable_episode,
        idempotency_key="valid-transition",
        state=EpisodeState.VALIDATED.value,
        **base_values,
    )
    connection.commit()
    _assert_rejected(
        connection,
        "UPDATE episodes SET state = %s, qa_evidence = NULL WHERE id = %s",
        (EpisodeState.QA_PASSED.value, mutable_episode),
        CheckViolation,
    )

    created_at = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)
    updated_at = created_at + timedelta(minutes=5)
    failure = StructuredFailure(
        code="render_failed",
        stage="render",
        message="render evidence is incomplete",
        retriable=False,
        details={"attempt": 1},
    )
    record = EpisodeRecord(
        tenant_id=tenant_a,
        project_id=project_a,
        episode_id=uuid7(),
        idempotency_key="complete-snapshot",
        profile_name="default",
        source_sha256=source_a_sha256,
        source_bytes=8,
        source_content=b"source-a",
        request_fingerprint=hashlib.sha256(b"complete-record").hexdigest(),
        state=EpisodeState.FAILED,
        version=1,
        created_at=created_at,
        updated_at=updated_at,
        qa_evidence={"critical_token_accuracy": 1.0},
        package_sha256=hashlib.sha256(b"package").hexdigest(),
        failure=failure,
    )
    _insert_episode(
        connection,
        episode_id=record.episode_id,
        tenant_id=record.tenant_id,
        project_id=record.project_id,
        idempotency_key=record.idempotency_key,
        profile_name=record.profile_name,
        source_sha256=record.source_sha256,
        source_bytes=record.source_bytes,
        request_fingerprint=record.request_fingerprint,
        state=record.state.value,
        qa_evidence=dict(record.qa_evidence or {}),
        package_sha256=record.package_sha256,
        failure_evidence=record.failure.to_dict() if record.failure else None,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
    connection.commit()

    stored = connection.execute(
        "SELECT tenant_id, project_id, idempotency_key, profile_name, source_sha256, "
        "source_bytes, request_fingerprint, state, qa_evidence, package_sha256, "
        "failure_evidence, version, created_at, updated_at FROM episodes WHERE id = %s",
        (record.episode_id,),
    ).fetchone()
    assert stored is not None
    assert stored[0:4] == (
        record.tenant_id,
        record.project_id,
        record.idempotency_key,
        record.profile_name,
    )
    assert str(stored[4]).strip() == record.source_sha256
    assert stored[5:8] == (
        record.source_bytes,
        record.request_fingerprint,
        record.state.value,
    )
    assert stored[8] == dict(record.qa_evidence or {})
    assert str(stored[9]).strip() == record.package_sha256
    assert stored[10] == record.failure.to_dict()
    assert stored[11:] == (record.version, record.created_at, record.updated_at)
