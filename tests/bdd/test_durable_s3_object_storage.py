"""BDD bindings for durable S3 bytes plus PostgreSQL object references."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.durable_s3_object_storage import DurableS3ObjectStore
from poddown.object_storage import ObjectNotFound, ObjectRef, ObjectStore
from poddown.postgres_objects import ObjectReferenceNotFound

scenarios("../features/durable_s3_object_storage.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
DATA = b"durable s3 bytes"


class _BlobStore(ObjectStore):
    def __init__(self) -> None:
        self.objects: dict[str, tuple[ObjectRef, bytes]] = {}

    def put(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ) -> ObjectRef:
        from hashlib import sha256

        digest = sha256(data).hexdigest()
        reference = ObjectRef(
            tenant_id,
            project_id,
            name,
            media_type,
            len(data),
            digest,
            f"tenants/{tenant_id}/projects/{project_id}/objects/{digest[:2]}/{digest}",
        )
        existing = self.objects.get(reference.storage_key)
        if existing is not None and existing[1] != data:
            raise ValueError("blob conflict")
        self.objects[reference.storage_key] = (reference, data)
        return reference

    def read(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> bytes:
        if reference.tenant_id != tenant_id or reference.project_id != project_id:
            raise ValueError("scope")
        stored = self.objects.get(reference.storage_key)
        if stored is None:
            raise ObjectNotFound("blob missing")
        return stored[1]

    def delete(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> None:
        self.read(tenant_id, project_id, reference)
        del self.objects[reference.storage_key]


class _References:
    def __init__(self) -> None:
        self.references: dict[tuple[UUID, UUID, str], ObjectRef] = {}

    def record(self, reference: ObjectRef) -> ObjectRef:
        key = (reference.tenant_id, reference.project_id, reference.sha256)
        existing = self.references.get(key)
        if existing is not None and existing != reference:
            raise ValueError("reference conflict")
        self.references[key] = reference
        return reference

    def get(self, tenant_id: UUID, project_id: UUID, sha256: str) -> ObjectRef:
        try:
            return self.references[(tenant_id, project_id, sha256)]
        except KeyError as error:
            raise ObjectReferenceNotFound("reference missing") from error

    def remove(self, reference: ObjectRef) -> None:
        del self.references[
            (reference.tenant_id, reference.project_id, reference.sha256)
        ]


@given("a fake blob store and durable object-reference repository")
def durable_store(context: Any) -> None:
    blobs = _BlobStore()
    references = _References()
    context.values["blobs"] = blobs
    context.values["references"] = references
    context.values["store"] = DurableS3ObjectStore(blobs, references)


@when("I put and read a durable object twice")
def put_read_durable(context: Any) -> None:
    store = context.values["store"]
    first = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=DATA,
    )
    second = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=DATA,
    )
    context.values["first"] = first
    context.values["second"] = second
    context.values["read"] = store.read(TENANT, PROJECT, first)


@then("the durable object and reference replay identically")
def durable_replays(context: Any) -> None:
    assert context.values["first"] == context.values["second"]
    assert context.values["read"] == DATA
    assert len(context.values["references"].references) == 1


@when("I read an unrecorded durable object")
def read_unrecorded(context: Any) -> None:
    reference = context.values["blobs"].put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=DATA,
    )
    with pytest.raises(ObjectNotFound, match="reference"):
        context.values["store"].read(TENANT, PROJECT, reference)


@then("the durable object read is rejected")
def unrecorded_rejected(context: Any) -> None:
    assert context.values["references"].references == {}
