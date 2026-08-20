"""Unit contracts for the transaction-scoped PostgreSQL tenant context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from uuid import UUID, uuid4

import pytest

from poddown.tenant_context import TenantContextError, tenant_transaction

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
OTHER_TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11")


class RecordingCursor:
    """Record DB-API SQL calls without connecting to a database."""

    def __init__(self, calls: list[tuple[str, str, tuple[object, ...]]]) -> None:
        self._calls = calls

    def execute(self, operation: str, parameters: Sequence[object] = ()) -> object:
        self._calls.append(("execute", operation, tuple(parameters)))
        return object()


class RecordingConnection:
    """Record transaction completion for a reusable simulated connection."""

    def __init__(
        self, database_kind: Literal["postgresql", "sqlite", "test"] = "test"
    ) -> None:
        self.poddown_database_kind = database_kind
        self.calls: list[tuple[str, str, tuple[object, ...]]] = []

    def cursor(self) -> RecordingCursor:
        self.calls.append(("cursor", "", ()))
        return RecordingCursor(self.calls)

    def commit(self) -> None:
        self.calls.append(("commit", "", ()))

    def rollback(self) -> None:
        self.calls.append(("rollback", "", ()))


def test_postgresql_sets_parameterized_tenant_context_before_application_sql() -> None:
    connection = RecordingConnection("postgresql")

    with tenant_transaction(
        connection, TENANT_ID, database_kind="postgresql"
    ) as cursor:
        cursor.execute("SELECT tenant_id FROM episodes WHERE id = %s", ("episode",))

    assert connection.calls == [
        ("cursor", "", ()),
        ("execute", "BEGIN", ()),
        (
            "execute",
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(TENANT_ID),),
        ),
        (
            "execute",
            "SELECT tenant_id FROM episodes WHERE id = %s",
            ("episode",),
        ),
        ("commit", "", ()),
    ]


def test_postgresql_context_is_set_once_per_transaction_and_clears_on_reuse() -> None:
    connection = RecordingConnection("postgresql")

    with tenant_transaction(
        connection, TENANT_ID, database_kind="postgresql"
    ) as cursor:
        cursor.execute("SELECT 1")
        cursor.execute("SELECT 2")
    with tenant_transaction(
        connection, OTHER_TENANT_ID, database_kind="postgresql"
    ) as cursor:
        cursor.execute("SELECT 3")

    settings = [call for call in connection.calls if "set_config" in call[1]]
    assert settings == [
        (
            "execute",
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(TENANT_ID),),
        ),
        (
            "execute",
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(OTHER_TENANT_ID),),
        ),
    ]
    assert [call[0] for call in connection.calls].count("commit") == 2


def test_postgresql_rolls_back_transaction_local_context_after_failure() -> None:
    connection = RecordingConnection("postgresql")

    with (
        pytest.raises(RuntimeError, match="stop"),
        tenant_transaction(connection, TENANT_ID, database_kind="postgresql") as cursor,
    ):
        cursor.execute("SELECT 1")
        raise RuntimeError("stop")

    assert connection.calls[-1] == ("rollback", "", ())
    assert ("commit", "", ()) not in connection.calls


@pytest.mark.parametrize("database_kind", ["sqlite", "test"])
def test_non_postgresql_boundary_does_not_claim_row_level_security(
    database_kind: Literal["sqlite", "test"],
) -> None:
    connection = RecordingConnection(database_kind)

    with tenant_transaction(
        connection, TENANT_ID, database_kind=database_kind
    ) as cursor:
        cursor.execute("SELECT 1")

    assert all("set_config" not in operation for _, operation, _ in connection.calls)
    assert connection.calls == [
        ("cursor", "", ()),
        ("execute", "BEGIN", ()),
        ("execute", "SELECT 1", ()),
        ("commit", "", ()),
    ]


@pytest.mark.parametrize("tenant_id", ["not-a-uuid", object()])
def test_transaction_boundary_rejects_unvalidated_tenant_id(tenant_id: object) -> None:
    connection = RecordingConnection()

    with (
        pytest.raises(TenantContextError, match="tenant_id must be a UUID"),
        tenant_transaction(connection, tenant_id, database_kind="postgresql"),
    ):
        pass

    assert connection.calls == []


def test_transaction_boundary_rejects_a_non_uuid7_tenant_id() -> None:
    """Catch an RLS context being set from a legacy UUID identity."""
    connection = RecordingConnection()

    with (
        pytest.raises(TenantContextError, match="tenant_id must be a UUIDv7"),
        tenant_transaction(connection, uuid4(), database_kind="postgresql"),
    ):
        pass

    assert connection.calls == []


def test_transaction_boundary_rejects_an_unknown_database_kind() -> None:
    """Catch arbitrary caller strings silently disabling PostgreSQL RLS setup."""
    connection = RecordingConnection()

    with (
        pytest.raises(TenantContextError, match="database kind is invalid"),
        tenant_transaction(connection, TENANT_ID, database_kind="mysql"),  # type: ignore[arg-type]
    ):
        pass

    assert connection.calls == []


def test_transaction_boundary_rejects_a_kind_that_does_not_match_connection() -> None:
    connection = RecordingConnection("test")

    with (
        pytest.raises(TenantContextError, match="does not match connection"),
        tenant_transaction(connection, TENANT_ID, database_kind="postgresql"),
    ):
        pass

    assert connection.calls == []
