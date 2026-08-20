"""BDD coverage for transaction-scoped PostgreSQL tenant context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.tenant_context import TenantContextError, tenant_transaction

scenarios("../features/tenant_context.feature")

FIRST_TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
SECOND_TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11")


class _Cursor:
    def __init__(self, calls: list[tuple[str, str, tuple[object, ...]]]) -> None:
        self._calls = calls

    def execute(self, operation: str, parameters: Sequence[object] = ()) -> object:
        self._calls.append(("execute", operation, tuple(parameters)))
        return object()


class _Connection:
    def __init__(self, database_kind: str) -> None:
        self.poddown_database_kind = database_kind
        self.calls: list[tuple[str, str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        self.calls.append(("cursor", "", ()))
        return _Cursor(self.calls)

    def commit(self) -> None:
        self.calls.append(("commit", "", ()))

    def rollback(self) -> None:
        self.calls.append(("rollback", "", ()))


class _Context(Protocol):
    values: dict[str, _Connection]


@given("a reusable PostgreSQL DB-API connection")
def postgresql_connection(context: _Context) -> None:
    context.values["connection"] = _Connection("postgresql")


@when("I run tenant work for two tenants in separate transactions")
def run_postgresql_tenant_work(context: _Context) -> None:
    connection = context.values["connection"]
    with tenant_transaction(
        connection, FIRST_TENANT, database_kind="postgresql"
    ) as cursor:
        cursor.execute("SELECT 1")
    with tenant_transaction(
        connection, SECOND_TENANT, database_kind="postgresql"
    ) as cursor:
        cursor.execute("SELECT 2")


@then("each transaction sets one parameterized local tenant context before its work")
def tenant_context_precedes_work(context: _Context) -> None:
    calls = context.values["connection"].calls
    settings = [index for index, call in enumerate(calls) if "set_config" in call[1]]
    assert len(settings) == 2
    for setting in settings:
        assert calls[setting][1] == "SELECT set_config('app.tenant_id', %s, true)"
        assert calls[setting][2]
        assert calls[setting + 1][1].startswith("SELECT ")


@then("the reused connection commits before the next tenant context begins")
def connection_reuse_does_not_leak_context(context: _Context) -> None:
    calls = context.values["connection"].calls
    first_setting = next(
        index for index, call in enumerate(calls) if "set_config" in call[1]
    )
    second_setting = next(
        index
        for index, call in enumerate(
            calls[first_setting + 1 :], start=first_setting + 1
        )
        if "set_config" in call[1]
    )
    assert calls[second_setting - 1] == ("execute", "BEGIN", ())
    assert any(
        call[0] == "commit" for call in calls[first_setting + 1 : second_setting]
    )


@given("a local SQLite DB-API connection")
def sqlite_connection(context: _Context) -> None:
    context.values["connection"] = _Connection("sqlite")


@when("I run tenant work in the local transaction boundary")
def run_sqlite_tenant_work(context: _Context) -> None:
    connection = context.values["connection"]
    with tenant_transaction(connection, FIRST_TENANT, database_kind="sqlite") as cursor:
        cursor.execute("SELECT 1")


@then("the local boundary does not set a PostgreSQL tenant context")
def sqlite_has_no_postgresql_context(context: _Context) -> None:
    calls = context.values["connection"].calls
    assert all("set_config" not in operation for _, operation, _ in calls)


@given("a test DB-API connection")
def test_connection(context: _Context) -> None:
    context.values["connection"] = _Connection("test")


@when("I request a PostgreSQL tenant context on the test connection")
def request_mismatched_tenant_context(context: _Context) -> None:
    with (
        pytest.raises(TenantContextError, match="does not match connection"),
        tenant_transaction(
            context.values["connection"], FIRST_TENANT, database_kind="postgresql"
        ),
    ):
        pass


@then("the tenant context rejects the mismatched database kind")
def mismatched_tenant_context_rejected(context: _Context) -> None:
    assert context.values["connection"].calls == []
