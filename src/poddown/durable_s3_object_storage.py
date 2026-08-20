"""Durable composition of S3-compatible bytes and PostgreSQL references."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from poddown.object_storage import (
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectRef,
    ObjectStorageError,
    ObjectStore,
)
from poddown.postgres_objects import ObjectReferenceNotFound


class ObjectReferencePort(Protocol):
    """Durable reference operations required beside the blob store."""

    def record(self, reference: ObjectRef) -> ObjectRef:
        """Record or replay one immutable reference."""

    def get(self, tenant_id: UUID, project_id: UUID, sha256: str) -> ObjectRef:
        """Read one reference in tenant/project scope."""

    def remove(self, reference: ObjectRef) -> None:
        """Remove one reference after the blob deletion succeeds."""


class DurableS3ObjectStore(ObjectStore):
    """Require a durable reference for every S3-compatible object read."""

    def __init__(
        self,
        blob_store: ObjectStore,
        references: ObjectReferencePort,
    ) -> None:
        if not hasattr(blob_store, "put"):
            raise TypeError("blob_store must implement ObjectStore")
        if not all(
            hasattr(references, method) for method in ("record", "get", "remove")
        ):
            raise TypeError("references must implement ObjectReferencePort")
        self._blob_store = blob_store
        self._references = references

    def put(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ) -> ObjectRef:
        """Write the blob, then persist its exact immutable reference."""
        reference = self._blob_store.put(
            tenant_id,
            project_id,
            name=name,
            media_type=media_type,
            data=data,
        )
        try:
            recorded = self._references.record(reference)
        except Exception as error:
            raise ObjectStorageError(
                "object reference persistence failed after blob write"
            ) from error
        if recorded != reference:
            raise ObjectIntegrityError("persisted object reference does not match blob")
        return recorded

    def read(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> bytes:
        """Read only when durable metadata exactly authorizes the blob."""
        try:
            recorded = self._references.get(
                tenant_id,
                project_id,
                reference.sha256,
            )
        except ObjectReferenceNotFound as error:
            raise ObjectNotFound("durable object reference is missing") from error
        if recorded != reference:
            raise ObjectIntegrityError("durable object reference does not match")
        return self._blob_store.read(tenant_id, project_id, recorded)

    def delete(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> None:
        """Delete a verified blob before removing its durable reference."""
        self.read(tenant_id, project_id, reference)
        self._blob_store.delete(tenant_id, project_id, reference)
        try:
            self._references.remove(reference)
        except Exception as error:
            raise ObjectStorageError(
                "object reference cleanup failed after blob deletion"
            ) from error


__all__ = ["DurableS3ObjectStore", "ObjectReferencePort"]
