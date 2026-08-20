"""BDD bindings for the offline PostgreSQL schema contract."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

from pytest_bdd import given, scenarios, then, when

from poddown.postgres_schema import (
    MIGRATIONS,
    MigrationRunner,
    set_transaction_tenant,
)

scenarios("../features/postgres_schema.feature")


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self._rows: list[tuple[int]] = []

    def execute(self, sql: str, params=()) -> _Cursor:
        self.connection.statements.append((sql, tuple(params)))
        normalized = " ".join(sql.split()).casefold()
        if normalized.startswith("select version, name"):
            names = {migration.version: migration.name for migration in MIGRATIONS}
            self._rows = [
                (version, names[version]) for version in self.connection.applied
            ]
        elif normalized.startswith("insert into poddown_schema_migrations"):
            self.connection.applied.add(int(params[0]))
        return self

    def fetchall(self) -> list[tuple[int]]:
        return self._rows


class _Connection:
    def __init__(self) -> None:
        self.applied: set[int] = set()
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self


@given("a recording PostgreSQL migration connection")
def recording_connection(context) -> None:
    context.values["connection"] = _Connection()


@when("I apply the PodDown migrations twice")
def apply_migrations(context) -> None:
    runner = MigrationRunner(MIGRATIONS)
    runner.apply(context.values["connection"])
    runner.apply(context.values["connection"])


@then("each migration is recorded once in version order")
def migrations_are_ordered(context) -> None:
    assert sorted(context.values["connection"].applied) == [
        migration.version for migration in MIGRATIONS
    ]
    inserts = [
        statement
        for statement in context.values["connection"].statements
        if statement[0]
        .lstrip()
        .casefold()
        .startswith("insert into poddown_schema_migrations")
    ]
    assert len(inserts) == len(MIGRATIONS)


@then("tenant RLS policies are present in the schema")
def rls_policies_are_present(context) -> None:
    sql = "\n".join(
        statement[0] for statement in context.values["connection"].statements
    )
    assert "enable row level security" in sql.casefold()
    assert "current_setting('poddown.tenant_id', true)" in sql.casefold()
    normalized = sql.casefold()
    assert normalized.index(
        "create table if not exists object_inventory"
    ) < normalized.index("alter table object_inventory enable row level security")


@when("I set the tenant context for a UUIDv7 tenant")
def set_tenant_context(context) -> None:
    tenant = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
    set_transaction_tenant(context.values["connection"], tenant)


@then("the connection receives a transaction-local tenant setting")
def tenant_setting_is_local(context) -> None:
    matching = [
        statement
        for statement in context.values["connection"].statements
        if "set_config('poddown.tenant_id'" in statement[0].casefold()
    ]
    assert matching == [
        (
            "SELECT set_config('poddown.tenant_id', %s, true)",
            ("018f2c8b-7b46-7cc5-b2e1-111111111111",),
        )
    ]
