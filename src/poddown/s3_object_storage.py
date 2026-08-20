"""S3-compatible object-store port with checksum and tenant-bound validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID

from poddown.object_storage import (
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectRef,
    ObjectScopeError,
    ObjectStore,
    ObjectValidationError,
    storage_key_for,
    validate_sha256,
)
from poddown.postgres_objects import (
    ObjectInventoryRepository,
    ObjectReferenceListing,
    OrphanCleanupService,
    OrphanCollectionReport,
)


class S3StorageError(ObjectValidationError):
    """Base error for S3-compatible storage configuration and transport failures."""


@dataclass(frozen=True, slots=True)
class S3StorageSettings:
    """Secret-reference-only settings for a MinIO or S3-compatible endpoint."""

    endpoint: str
    bucket: str
    secret_ref: str
    region: str = "us-east-1"
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("S3 endpoint must be an HTTP(S) URL")
        local_hosts = {"localhost", "127.0.0.1", "::1", "minio", "minio.local"}
        if parsed.scheme != "https" and (
            not self.allow_insecure_local or host.casefold() not in local_hosts
        ):
            raise ValueError("remote S3 endpoints require HTTPS")
        if not self.bucket or "/" in self.bucket or "\\" in self.bucket:
            raise ValueError("S3 bucket is invalid")
        if not self.secret_ref.startswith("secret://"):
            raise ValueError("S3 credentials require a secret reference")
        if not self.region.strip():
            raise ValueError("S3 region is required")

    def to_record(self) -> dict[str, object]:
        """Return safe settings metadata without credentials."""
        return {
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "secret_ref": self.secret_ref,
            "region": self.region,
            "allow_insecure_local": self.allow_insecure_local,
        }


@dataclass(frozen=True, slots=True)
class S3StoredObject:
    """Bytes and metadata returned by an injected S3-compatible transport."""

    data: bytes
    metadata: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise S3StorageError("S3 object data must be bytes")
        if not isinstance(self.metadata, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.metadata.items()
        ):
            raise S3StorageError("S3 object metadata is invalid")


class S3Transport(Protocol):
    """Injected transport boundary implemented by MinIO/S3 clients."""

    def put_object(
        self,
        key: str,
        data: bytes,
        metadata: dict[str, str],
    ) -> None:
        """Create or replace one exact object at a canonical key."""

    def get_object(self, key: str) -> S3StoredObject:
        """Return exact bytes and provider metadata for one key."""

    def delete_object(self, key: str) -> None:
        """Delete one exact object key."""

    def list_objects(self, prefix: str) -> tuple[str, ...]:
        """List object keys under one exact prefix."""


def _expected_metadata(reference: ObjectRef) -> dict[str, str]:
    return {
        "poddown-sha256": reference.sha256,
        "poddown-byte-count": str(reference.byte_count),
        "poddown-media-type": reference.media_type,
        "poddown-name": reference.name,
        "poddown-tenant-id": str(reference.tenant_id),
        "poddown-project-id": str(reference.project_id),
    }


class S3ObjectStore(ObjectStore):
    """Content-addressed object store over an injected S3-compatible transport."""

    def __init__(
        self,
        transport: S3Transport,
        *,
        endpoint: str,
        bucket: str,
        secret_ref: str,
        region: str = "us-east-1",
        allow_insecure_local: bool = False,
    ) -> None:
        self.settings = S3StorageSettings(
            endpoint=endpoint,
            bucket=bucket,
            secret_ref=secret_ref,
            region=region,
            allow_insecure_local=allow_insecure_local,
        )
        self._transport = transport

    def put(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ) -> ObjectRef:
        """Put or verify one content-addressed object with exact metadata."""
        if not isinstance(data, bytes):
            raise ObjectValidationError("data must be bytes")
        digest = sha256(data).hexdigest()
        reference = ObjectRef(
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            media_type=media_type,
            byte_count=len(data),
            sha256=digest,
            storage_key=storage_key_for(tenant_id, project_id, digest),
        )
        expected = _expected_metadata(reference)
        try:
            existing = self._transport.get_object(reference.storage_key)
        except (KeyError, ObjectNotFound):
            self._transport.put_object(reference.storage_key, data, expected)
            existing = self._transport.get_object(reference.storage_key)
        self._verify(reference, existing)
        if existing.data != data:
            raise ObjectIntegrityError("S3 object bytes do not match requested data")
        return reference

    def read(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> bytes:
        """Read one object only after enforcing scope, metadata and checksum."""
        self._require_scope(tenant_id, project_id, reference)
        try:
            stored = self._transport.get_object(reference.storage_key)
        except (KeyError, ObjectNotFound) as error:
            raise ObjectNotFound("S3 object is missing") from error
        self._verify(reference, stored)
        return stored.data

    def delete(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> None:
        """Delete only an object that first passes the full integrity check."""
        self.read(tenant_id, project_id, reference)
        try:
            self._transport.delete_object(reference.storage_key)
        except (KeyError, ObjectNotFound) as error:
            raise ObjectNotFound("S3 object is missing") from error

    def list_keys(self, tenant_id: UUID, project_id: UUID) -> tuple[str, ...]:
        """List only canonical content-addressed keys in one project scope."""
        prefix = self._project_prefix(tenant_id, project_id)
        try:
            keys = self._transport.list_objects(prefix)
        except Exception as error:
            if isinstance(error, (KeyError, ObjectNotFound)):
                raise ObjectNotFound("S3 object inventory is missing") from error
            raise S3StorageError("S3 object inventory failed") from error
        canonical: set[str] = set()
        for key in keys:
            if not isinstance(key, str):
                raise ObjectIntegrityError("S3 object inventory key is invalid")
            self._digest_for_key(tenant_id, project_id, key)
            canonical.add(key)
        return tuple(sorted(canonical))

    def delete_key(self, tenant_id: UUID, project_id: UUID, key: str) -> None:
        """Verify and delete one unreferenced canonical inventory key."""
        digest = self._digest_for_key(tenant_id, project_id, key)
        try:
            stored = self._transport.get_object(key)
        except (KeyError, ObjectNotFound) as error:
            raise ObjectNotFound("S3 object is missing") from error
        name = stored.metadata.get("poddown-name")
        media_type = stored.metadata.get("poddown-media-type")
        byte_count = stored.metadata.get("poddown-byte-count")
        if name is None or media_type is None or byte_count is None:
            raise ObjectIntegrityError("S3 inventory object metadata is incomplete")
        try:
            reference = ObjectRef(
                tenant_id=tenant_id,
                project_id=project_id,
                name=name,
                media_type=media_type,
                byte_count=int(byte_count),
                sha256=digest,
                storage_key=key,
            )
        except (ObjectValidationError, TypeError, ValueError) as error:
            raise ObjectIntegrityError(
                "S3 inventory object metadata is invalid"
            ) from error
        self._verify(reference, stored)
        self.delete(tenant_id, project_id, reference)

    @staticmethod
    def _project_prefix(tenant_id: UUID, project_id: UUID) -> str:
        marker = "0" * 64
        return storage_key_for(tenant_id, project_id, marker).rsplit("/", 2)[0] + "/"

    @classmethod
    def _digest_for_key(cls, tenant_id: UUID, project_id: UUID, key: str) -> str:
        if not isinstance(key, str):
            raise ObjectIntegrityError("S3 inventory key is invalid")
        prefix = cls._project_prefix(tenant_id, project_id)
        if not key.startswith(prefix):
            raise ObjectScopeError("S3 inventory key is outside caller scope")
        suffix = key[len(prefix) :].split("/")
        if len(suffix) != 2 or suffix[0] != suffix[1][:2]:
            raise ObjectIntegrityError("S3 inventory key is not canonical")
        try:
            digest = validate_sha256(suffix[1])
        except ObjectValidationError as error:
            raise ObjectIntegrityError("S3 inventory key is not canonical") from error
        if storage_key_for(tenant_id, project_id, digest) != key:
            raise ObjectIntegrityError("S3 inventory key is not canonical")
        return digest

    @staticmethod
    def _require_scope(
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> None:
        if reference.tenant_id != tenant_id or reference.project_id != project_id:
            raise ObjectScopeError("object reference is outside caller scope")

    @staticmethod
    def _verify(reference: ObjectRef, stored: S3StoredObject) -> None:
        expected = _expected_metadata(reference)
        if any(stored.metadata.get(key) != value for key, value in expected.items()):
            raise ObjectIntegrityError("S3 object metadata failed integrity validation")
        if len(stored.data) != reference.byte_count:
            raise ObjectIntegrityError(
                "S3 object byte count failed integrity validation"
            )
        if sha256(stored.data).hexdigest() != reference.sha256:
            raise ObjectIntegrityError("S3 object checksum failed integrity validation")


class S3ObjectMaintenance:
    """Compose S3 inventory with durable references for explicit cleanup runs."""

    def __init__(
        self,
        store: S3ObjectStore,
        *,
        references: ObjectReferenceListing,
        inventory: ObjectInventoryRepository,
    ) -> None:
        if not isinstance(store, S3ObjectStore):
            raise TypeError("store must be an S3ObjectStore")
        self._store = store
        self._cleanup = OrphanCleanupService(
            references=references,
            inventory=inventory,
        )

    def collect_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        now: datetime,
        grace_period: timedelta,
        observed_at: datetime,
    ) -> OrphanCollectionReport:
        """Run one explicit, current-inventory, reference-aware cleanup pass."""
        return self._cleanup.collect_project(
            tenant_id,
            project_id,
            self._store.list_keys(tenant_id, project_id),
            now=now,
            grace_period=grace_period,
            delete=lambda key: self._store.delete_key(tenant_id, project_id, key),
            observed_at=observed_at,
        )


__all__ = [
    "S3ObjectMaintenance",
    "S3ObjectStore",
    "S3StorageError",
    "S3StorageSettings",
    "S3StoredObject",
    "S3Transport",
]
