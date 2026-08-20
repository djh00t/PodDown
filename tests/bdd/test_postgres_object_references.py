"""BDD bindings for PostgreSQL object references and safe orphan collection."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.object_storage import ObjectRef
from poddown.postgres_objects import (
    InventoryObject,
    ObjectReferenceError,
    ObjectReferenceNotFound,
    OrphanCleanupService,
    OrphanGarbageCollector,
    PostgresObjectInventoryRepository,
    PostgresObjectReferenceRepository,
)

scenarios("../features/postgres_object_references.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
CREATED = datetime(2026, 8, 14, tzinfo=UTC)
DATA = b"referenced object"


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.last: tuple[object, ...] | None = None
        self.rows: list[tuple[object, ...]] = []
        self.rowcount = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> _Cursor:
        normalized = " ".join(sql.split()).casefold()
        self.connection.statements.append((sql, params))
        self.last = None
        self.rows = []
        self.rowcount = 0
        if normalized.startswith("select set_config"):
            return self
        if normalized.startswith("select sha256"):
            if "and sha256 = %s" in normalized:
                key = (str(params[0]), str(params[1]), str(params[2]))
                row = self.connection.refs.get(key)
                self.last = row
                self.rows = [row] if row is not None else []
            else:
                prefix = (str(params[0]), str(params[1]))
                self.rows = [
                    row
                    for (tenant, project, _digest), row in sorted(
                        self.connection.refs.items()
                    )
                    if (tenant, project) == prefix
                ]
            return self
        if normalized.startswith("select storage_key, first_seen_at"):
            prefix = (str(params[0]), str(params[1]))
            self.rows = [
                (_key, row[0])
                for (tenant, project, _key), row in sorted(
                    self.connection.inventory.items()
                )
                if (tenant, project) == prefix
            ]
            return self
        if normalized.startswith("insert into object_references"):
            key = (str(params[0]), str(params[1]), str(params[2]))
            self.connection.refs.setdefault(
                key,
                (params[2], params[3], params[4], params[5], params[6]),
            )
            self.rowcount = 1
            return self
        if normalized.startswith("delete from object_references"):
            key = (str(params[0]), str(params[1]), str(params[2]))
            if key in self.connection.refs:
                del self.connection.refs[key]
                self.rowcount = 1
            return self
        if normalized.startswith("insert into object_inventory"):
            key = (str(params[0]), str(params[1]), str(params[2]))
            existing = self.connection.inventory.get(key)
            first_seen = existing[0] if existing is not None else params[3]
            self.connection.inventory[key] = (first_seen, params[4])
            self.rowcount = 1
            return self
        if normalized.startswith("delete from object_inventory"):
            key = (str(params[0]), str(params[1]), str(params[2]))
            if key in self.connection.inventory:
                del self.connection.inventory[key]
                self.rowcount = 1
            return self
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[object, ...] | None:
        return self.last

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class _Connection:
    def __init__(self) -> None:
        self.refs: dict[tuple[str, str, str], tuple[object, ...]] = {}
        self.inventory: dict[tuple[str, str, str], tuple[object, ...]] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self


def _reference(name: str = "episode.mp3") -> ObjectRef:
    from hashlib import sha256

    digest = sha256(DATA).hexdigest()
    return ObjectRef(
        tenant_id=TENANT,
        project_id=PROJECT,
        name=name,
        media_type="audio/mpeg",
        byte_count=len(DATA),
        sha256=digest,
        storage_key=(
            f"tenants/{TENANT}/projects/{PROJECT}/objects/{digest[:2]}/{digest}"
        ),
    )


@given("a recording PostgreSQL object-reference repository")
def recording_repository(context: Any) -> None:
    connection = _Connection()
    context.values["connection"] = connection
    context.values["repository"] = PostgresObjectReferenceRepository(lambda: connection)
    context.values["reference"] = _reference()


@when("I record and replay a PostgreSQL object reference")
def record_replay(context: Any) -> None:
    repository = context.values["repository"]
    reference = context.values["reference"]
    context.values["first"] = repository.record(reference)
    context.values["second"] = repository.record(reference)


@then("the object reference is identical and tenant scoped")
def reference_replays(context: Any) -> None:
    assert context.values["first"] == context.values["second"]
    assert context.values["first"].tenant_id == TENANT
    assert repository_statements(context, "set_config")


def repository_statements(
    context: Any, fragment: str
) -> list[tuple[str, tuple[object, ...]]]:
    return [
        statement
        for statement in context.values["connection"].statements
        if fragment in statement[0]
    ]


@when("I record and remove a PostgreSQL object reference")
def record_remove(context: Any) -> None:
    repository = context.values["repository"]
    reference = context.values["reference"]
    repository.record(reference)
    repository.remove(reference)
    with pytest.raises(ObjectReferenceNotFound):
        repository.get(TENANT, PROJECT, reference.sha256)


@then("the PostgreSQL object reference is absent")
def reference_absent(context: Any) -> None:
    assert context.values["connection"].refs == {}


@when("the persisted object metadata is malformed")
def malformed_reference(context: Any) -> None:
    reference = context.values["reference"]
    context.values["connection"].refs[(str(TENANT), str(PROJECT), reference.sha256)] = (
        reference.sha256,
        reference.storage_key,
        reference.media_type,
        "not-an-integer",
        reference.name,
    )
    with pytest.raises(ObjectReferenceError, match="invalid"):
        context.values["repository"].get(TENANT, PROJECT, reference.sha256)


@then("the PostgreSQL object reference is rejected")
def malformed_reference_rejected(context: Any) -> None:
    assert context.values["reference"].sha256


@given("a conservative orphan collector")
def conservative_collector(context: Any) -> None:
    context.values["collector"] = OrphanGarbageCollector()
    context.values["deleted"] = []


@when("I collect stale PostgreSQL object orphans")
def collect_orphans(context: Any) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    inventory = (
        InventoryObject("old-unreferenced", now - timedelta(days=2)),
        InventoryObject("old-referenced", now - timedelta(days=2)),
        InventoryObject("young-unreferenced", now - timedelta(hours=1)),
    )
    context.values["report"] = context.values["collector"].collect(
        inventory,
        {"old-referenced"},
        now=now,
        grace_period=timedelta(days=1),
        delete=context.values["deleted"].append,
    )


@then("only the old unreferenced object is deleted")
def only_old_orphan_deleted(context: Any) -> None:
    assert context.values["deleted"] == ["old-unreferenced"]
    assert context.values["report"].retained_keys == (
        "old-referenced",
        "young-unreferenced",
    )


@given("a recording PostgreSQL object-inventory repository")
def recording_inventory_repository(context: Any) -> None:
    connection = _Connection()
    context.values["connection"] = connection
    context.values["inventory_repository"] = PostgresObjectInventoryRepository(
        lambda: connection
    )


@when("I observe the same object on two inventory passes")
def observe_inventory_twice(context: Any) -> None:
    repository = context.values["inventory_repository"]
    first_seen = datetime(2026, 8, 12, tzinfo=UTC)
    second_seen = datetime(2026, 8, 14, tzinfo=UTC)
    repository.observe(
        TENANT,
        PROJECT,
        ("tenants/example/object-a",),
        observed_at=first_seen,
    )
    context.values["observed"] = repository.observe(
        TENANT,
        PROJECT,
        ("tenants/example/object-a",),
        observed_at=second_seen,
    )


@then("the original first-seen time is retained")
def first_seen_is_retained(context: Any) -> None:
    observed = context.values["observed"]
    assert len(observed) == 1
    assert observed[0].storage_key == "tenants/example/object-a"
    assert observed[0].discovered_at == datetime(2026, 8, 12, tzinfo=UTC)


@given("recording PostgreSQL reference and inventory repositories")
def recording_reference_and_inventory_repositories(context: Any) -> None:
    connection = _Connection()
    reference_repository = PostgresObjectReferenceRepository(lambda: connection)
    inventory_repository = PostgresObjectInventoryRepository(lambda: connection)
    reference = _reference()
    reference_repository.record(reference)
    context.values["connection"] = connection
    context.values["cleanup"] = OrphanCleanupService(
        references=reference_repository,
        inventory=inventory_repository,
    )
    context.values["referenced_key"] = reference.storage_key
    context.values["deleted"] = []


@when("I run scoped orphan collection")
def run_scoped_orphan_collection(context: Any) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    context.values["report"] = context.values["cleanup"].collect_project(
        TENANT,
        PROJECT,
        (context.values["referenced_key"], "old-unreferenced"),
        now=now,
        grace_period=timedelta(days=1),
        delete=context.values["deleted"].append,
        observed_at=now - timedelta(days=2),
    )


@then("only the unreferenced stale inventory key is deleted")
def scoped_orphan_collection_is_safe(context: Any) -> None:
    assert context.values["deleted"] == ["old-unreferenced"]
    assert context.values["report"].deleted_keys == ("old-unreferenced",)
    assert context.values["report"].retained_keys == (context.values["referenced_key"],)
