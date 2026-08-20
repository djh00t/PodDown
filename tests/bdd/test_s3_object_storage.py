"""BDD bindings for the S3-compatible object-store boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.object_storage import ObjectRef
from poddown.postgres_objects import InventoryObject
from poddown.s3_object_storage import S3ObjectMaintenance, S3ObjectStore, S3StoredObject

scenarios("../features/s3_object_storage.feature")


TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
OTHER_TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-999999999999")


class _Transport:
    def __init__(self) -> None:
        self.objects: dict[str, S3StoredObject] = {}

    def put_object(
        self,
        key: str,
        data: bytes,
        metadata: dict[str, str],
    ) -> None:
        self.objects[key] = S3StoredObject(data=data, metadata=metadata)

    def get_object(self, key: str) -> S3StoredObject:
        return self.objects[key]

    def delete_object(self, key: str) -> None:
        del self.objects[key]

    def list_objects(self, prefix: str) -> tuple[str, ...]:
        return tuple(sorted(key for key in self.objects if key.startswith(prefix)))


@given("a recording S3-compatible object transport")
def recording_transport(context) -> None:
    transport = _Transport()
    context.values["transport"] = transport
    context.values["store"] = S3ObjectStore(
        transport,
        endpoint="http://minio.local:9000",
        bucket="poddown",
        secret_ref="secret://minio/poddown",
        allow_insecure_local=True,
    )
    context.values["data"] = b"immutable audio bytes"


@when("I put and read the same object twice")
def put_and_read(context) -> None:
    store = context.values["store"]
    data = context.values["data"]
    context.values["first"] = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=data,
    )
    context.values["second"] = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=data,
    )
    context.values["read"] = store.read(TENANT, PROJECT, context.values["first"])


@then("the object reference and bytes are identical")
def exact_object_replays(context) -> None:
    assert context.values["first"] == context.values["second"]
    assert context.values["read"] == context.values["data"]


@when("the stored bytes are tampered before reading")
def tamper_object(context) -> None:
    store = context.values["store"]
    reference = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=context.values["data"],
    )
    context.values["reference"] = reference
    stored = context.values["transport"].objects[reference.storage_key]
    context.values["transport"].objects[reference.storage_key] = S3StoredObject(
        data=b"tampered", metadata=stored.metadata
    )
    with pytest.raises(ValueError, match="integrity"):
        store.read(TENANT, PROJECT, reference)


@then("object integrity fails closed")
def object_integrity_rejected(context) -> None:
    assert context.values["reference"].sha256


@when("another tenant reads the object")
def cross_tenant_read(context) -> None:
    store = context.values["store"]
    reference = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=context.values["data"],
    )
    with pytest.raises(ValueError, match="scope"):
        store.read(OTHER_TENANT, PROJECT, reference)


@then("object scope fails closed")
def object_scope_rejected(context) -> None:
    assert context.values["transport"].objects


@when("I inventory the tenant project objects")
def inventory_project_objects(context) -> None:
    store = context.values["store"]
    reference = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=context.values["data"],
    )
    context.values["keys"] = store.list_keys(TENANT, PROJECT)
    context.values["reference"] = reference


@then("only canonical project keys are returned")
def canonical_project_keys(context) -> None:
    assert context.values["keys"] == (context.values["reference"].storage_key,)


@when("I delete one unreferenced inventory key")
def delete_inventory_key(context) -> None:
    store = context.values["store"]
    reference = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=context.values["data"],
    )
    store.delete_key(TENANT, PROJECT, reference.storage_key)
    context.values["reference"] = reference


@then("the verified object is removed")
def verified_object_removed(context) -> None:
    assert (
        context.values["reference"].storage_key
        not in context.values["transport"].objects
    )


class _ReferenceRepository:
    def __init__(self, reference: ObjectRef) -> None:
        self.reference = reference

    def list_for_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
    ) -> tuple[ObjectRef, ...]:
        assert (tenant_id, project_id) == (TENANT, PROJECT)
        return (self.reference,)


class _InventoryRepository:
    def __init__(self) -> None:
        self.first_seen: dict[str, datetime] = {}

    def observe(
        self,
        tenant_id: UUID,
        project_id: UUID,
        storage_keys: tuple[str, ...],
        *,
        observed_at: datetime,
    ) -> tuple[InventoryObject, ...]:
        assert (tenant_id, project_id) == (TENANT, PROJECT)
        return tuple(
            InventoryObject(
                key,
                self.first_seen.setdefault(key, observed_at),
            )
            for key in sorted(storage_keys)
        )

    def remove(self, tenant_id: UUID, project_id: UUID, storage_key: str) -> None:
        assert (tenant_id, project_id) == (TENANT, PROJECT)
        self.first_seen.pop(storage_key, None)


@when("I run reference-aware S3 orphan cleanup")
def run_reference_aware_s3_cleanup(context) -> None:
    store = context.values["store"]
    referenced = store.put(
        TENANT,
        PROJECT,
        name="referenced.mp3",
        media_type="audio/mpeg",
        data=b"referenced bytes",
    )
    orphan = store.put(
        TENANT,
        PROJECT,
        name="orphan.mp3",
        media_type="audio/mpeg",
        data=b"orphan bytes",
    )
    inventory = _InventoryRepository()
    maintenance = S3ObjectMaintenance(
        store,
        references=_ReferenceRepository(referenced),
        inventory=inventory,
    )
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    context.values["report"] = maintenance.collect_project(
        TENANT,
        PROJECT,
        now=now,
        grace_period=timedelta(days=1),
        observed_at=now - timedelta(days=2),
    )
    context.values["referenced"] = referenced
    context.values["orphan"] = orphan


@then("only the stale unreferenced S3 object is removed")
def s3_cleanup_protects_references(context) -> None:
    keys = context.values["store"].list_keys(TENANT, PROJECT)
    assert keys == (context.values["referenced"].storage_key,)
    assert context.values["report"].deleted_keys == (
        context.values["orphan"].storage_key,
    )
