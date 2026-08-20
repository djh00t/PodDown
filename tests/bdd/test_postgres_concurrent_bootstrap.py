"""Opt-in BDD evidence for concurrent PostgreSQL runtime bootstrap."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from uuid import uuid4

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.postgres_runtime import initialize_postgres
from poddown.postgres_schema import MIGRATIONS

_POSTGRES_TEST_DSN = os.environ.get("PODDOWN_POSTGRES_TEST_DSN", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_DSN,
    reason="concurrent PostgreSQL bootstrap requires PODDOWN_POSTGRES_TEST_DSN",
)

scenarios("../features/postgres_concurrent_bootstrap.feature")


@pytest.fixture
def isolated_postgres_schema() -> Iterator[str]:
    """Create one schema shared by two independent bootstrap connections."""
    import psycopg

    admin = psycopg.connect(_POSTGRES_TEST_DSN, autocommit=False)
    schema = f"poddown_bootstrap_{uuid4().hex}"
    admin.execute(f'CREATE SCHEMA "{schema}"')
    admin.commit()
    try:
        yield schema
    finally:
        admin.execute("RESET search_path")
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.commit()
        admin.close()


@given("an isolated PostgreSQL schema for concurrent bootstrap")
def concurrent_schema(context, isolated_postgres_schema: str) -> None:
    """Store the schema used by both runtime initializers."""
    context.values["schema"] = isolated_postgres_schema


@when("two runtime components initialize PostgreSQL concurrently")
def concurrent_bootstrap(context) -> None:
    """Run two independent migration bootstraps against one schema."""
    import psycopg

    barrier = threading.Barrier(2)
    outcomes: list[tuple[int, ...]] = []
    errors: list[BaseException] = []

    def factory():
        connection = psycopg.connect(_POSTGRES_TEST_DSN, autocommit=False)
        connection.execute(f'SET search_path TO "{context.values["schema"]}"')
        connection.commit()
        return connection

    def bootstrap() -> None:
        try:
            barrier.wait(timeout=30)
            outcomes.append(initialize_postgres(factory))
        except BaseException as error:  # pragma: no cover - assertion reports it
            errors.append(error)

    threads = [threading.Thread(target=bootstrap) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    context.values["outcomes"] = outcomes
    context.values["errors"] = errors


@then("both bootstrap calls complete with the full migration catalog")
def concurrent_bootstrap_succeeds(context) -> None:
    """Require both callers to observe a valid, serialized catalog."""
    assert context.values["errors"] == []
    full_catalog = tuple(migration.version for migration in MIGRATIONS)
    assert sorted(context.values["outcomes"], key=len) == [(), full_catalog]
