"""Tenant-scoped content-addressed object storage ports and local adapter."""

from __future__ import annotations

import json
import os
import re
import stat
from base64 import b64encode
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_NAME_LENGTH = 255
_MAX_MEDIA_TYPE_LENGTH = 255
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | _O_NOFOLLOW
_METADATA_SUFFIX = ".metadata.json"
_S3_OBJECT_SCHEMA = "poddown.object"
_S3_OBJECT_SCHEMA_VERSION = "1.0"


class ObjectStorageError(ValueError):
    """Base error for typed object-storage contract failures."""


class ObjectValidationError(ObjectStorageError):
    """The object reference or put request is malformed."""


class ObjectScopeError(ObjectStorageError):
    """A caller attempted to use an object outside its tenant/project scope."""


class ObjectNotFound(ObjectStorageError, FileNotFoundError):
    """The referenced immutable object is absent."""


class ObjectIntegrityError(ObjectStorageError, RuntimeError):
    """Stored bytes do not match the immutable object reference."""


def _validate_uuid7(value: UUID, *, field: str) -> UUID:
    """Require a UUIDv7 tenant or project identifier."""
    if not isinstance(value, UUID) or value.version != 7:
        raise ObjectValidationError(f"{field} must be a UUIDv7")
    return value


def validate_sha256(value: str) -> str:
    """Validate one lowercase hexadecimal SHA-256 digest."""
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ObjectValidationError("sha256 must be a lowercase SHA-256 digest")
    return value


def _validate_name(value: str) -> str:
    """Validate display metadata without allowing path syntax."""
    if not isinstance(value, str) or not value or len(value) > _MAX_NAME_LENGTH:
        raise ObjectValidationError("object name is invalid")
    if "\x00" in value or "/" in value or "\\" in value or value in {".", ".."}:
        raise ObjectValidationError("object name contains path syntax")
    return value


def _validate_media_type(value: str) -> str:
    """Validate a bounded media-type metadata value."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_MEDIA_TYPE_LENGTH
        or any(character.isspace() for character in value)
        or value.count("/") != 1
    ):
        raise ObjectValidationError("media type is invalid")
    return value


def _validate_discovery_prefix(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_NAME_LENGTH:
        raise ObjectValidationError("object discovery prefix is invalid")
    if "\x00" in value or "/" in value or "\\" in value:
        raise ObjectValidationError("object discovery prefix contains path syntax")
    return value


def storage_key_for(
    tenant_id: UUID,
    project_id: UUID,
    digest: str,
) -> str:
    """Return the canonical tenant/project-scoped object key."""
    _validate_uuid7(tenant_id, field="tenant_id")
    _validate_uuid7(project_id, field="project_id")
    validate_sha256(digest)
    return f"tenants/{tenant_id}/projects/{project_id}/objects/{digest[:2]}/{digest}"


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """Immutable metadata needed to read one scoped object."""

    tenant_id: UUID
    project_id: UUID
    name: str
    media_type: str
    byte_count: int
    sha256: str
    storage_key: str

    def __post_init__(self) -> None:
        _validate_uuid7(self.tenant_id, field="tenant_id")
        _validate_uuid7(self.project_id, field="project_id")
        _validate_name(self.name)
        _validate_media_type(self.media_type)
        if type(self.byte_count) is not int or self.byte_count < 0:
            raise ObjectValidationError("byte_count must be a non-negative integer")
        validate_sha256(self.sha256)
        expected_key = storage_key_for(
            self.tenant_id,
            self.project_id,
            self.sha256,
        )
        if self.storage_key != expected_key:
            raise ObjectValidationError("storage_key is not canonical")

    def to_dict(self) -> dict[str, object]:
        """Return JSON-shaped immutable reference metadata."""
        return {
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "name": self.name,
            "media_type": self.media_type,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "storage_key": self.storage_key,
        }


class ObjectStore(Protocol):
    """Port implemented by local and future S3-compatible stores."""

    def put(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ) -> ObjectRef:
        """Store or replay one immutable object."""

    def read(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> bytes:
        """Read one object only within the caller's tenant/project scope."""

    def delete(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> None:
        """Delete one verified object only within the caller's scope."""


@runtime_checkable
class ObjectStoreDiscovery(Protocol):
    """Optional port for discovering verified object references by display name."""

    def list(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name_prefix: str,
    ) -> tuple[ObjectRef, ...]:
        """Return scoped immutable references whose names share the prefix."""


class S3Client(Protocol):
    """The minimal injected boto3 S3-client surface used by ``S3ObjectStore``."""

    def head_object(
        self,
        *,
        Bucket: str,
        Key: str,
        ChecksumMode: Literal["ENABLED"],
    ) -> Mapping[str, object]:
        """Return S3 object metadata for one bucket key."""

    def put_object(self, **kwargs: object) -> object:
        """Create one S3 object with its immutable metadata."""

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        ChecksumMode: Literal["ENABLED"],
        VersionId: str | None = None,
    ) -> Mapping[str, object]:
        """Return one S3 object and readable body stream."""

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        """Delete one S3 object by bucket key."""

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        """List S3 objects beneath one scoped prefix."""


class S3ObjectStore:
    """S3-backed implementation of the immutable tenant object-store port."""

    def __init__(self, client: S3Client, *, bucket: str) -> None:
        if not isinstance(bucket, str) or not bucket:
            raise ObjectValidationError("S3 bucket is invalid")
        self._bucket = bucket
        self._client = client

    def put(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ) -> ObjectRef:
        """Create or verify an immutable object with S3 checksum metadata."""
        reference = self._reference(
            tenant_id,
            project_id,
            name=name,
            media_type=media_type,
            data=data,
        )
        try:
            self._verify_stored_object(reference)
        except ObjectNotFound:
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=reference.storage_key,
                    Body=data,
                    Metadata=self._metadata(reference),
                    ChecksumAlgorithm="SHA256",
                    ChecksumSHA256=self._checksum(reference.sha256),
                    IfNoneMatch="*",
                )
            except Exception as error:
                if not self._is_precondition_failure(error):
                    raise ObjectIntegrityError(
                        "S3 object publication failed"
                    ) from error
        self._verify_stored_object(reference)
        return reference

    def read(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> bytes:
        """Read exact bytes after enforcing scope and persisted metadata."""
        self._validate_scope(tenant_id, project_id, reference)
        return self._verify_stored_object(reference)

    def _verify_stored_object(self, reference: ObjectRef) -> bytes:
        """Verify immutable metadata and bytes for one already-addressed object."""
        head_response = self._verify_head(reference)
        return self._read_bytes(
            reference,
            version_id=self._version_id(head_response),
        )

    def _read_bytes(self, reference: ObjectRef, *, version_id: str | None) -> bytes:
        """Fetch and hash object bytes after HEAD metadata verification."""
        try:
            if version_id is None:
                response = self._client.get_object(
                    Bucket=self._bucket,
                    Key=reference.storage_key,
                    ChecksumMode="ENABLED",
                )
            else:
                response = self._client.get_object(
                    Bucket=self._bucket,
                    Key=reference.storage_key,
                    ChecksumMode="ENABLED",
                    VersionId=version_id,
                )
        except Exception as error:
            raise self._read_error(error) from error
        self._verify_response_metadata(response, reference)
        if version_id is not None and response.get("VersionId") != version_id:
            raise ObjectIntegrityError("S3 object version does not match HEAD")
        body = response.get("Body")
        if not hasattr(body, "read"):
            raise ObjectIntegrityError("S3 object response body is invalid")
        data = body.read()
        if not isinstance(data, bytes):
            raise ObjectIntegrityError("S3 object response body is invalid")
        if (
            len(data) != reference.byte_count
            or sha256(data).hexdigest() != reference.sha256
        ):
            raise ObjectIntegrityError("S3 object bytes do not match reference")
        return data

    def delete(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> None:
        """Delete one verified immutable object only within the caller's scope."""
        self.read(tenant_id, project_id, reference)
        try:
            self._client.delete_object(
                Bucket=self._bucket,
                Key=reference.storage_key,
            )
        except Exception as error:
            raise ObjectIntegrityError("S3 object deletion failed") from error

    def list(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name_prefix: str,
    ) -> tuple[ObjectRef, ...]:
        """Discover verified references using S3 list plus checksum-bound HEADs."""
        _validate_uuid7(tenant_id, field="tenant_id")
        _validate_uuid7(project_id, field="project_id")
        _validate_discovery_prefix(name_prefix)
        prefix = f"tenants/{tenant_id}/projects/{project_id}/objects/"
        continuation: str | None = None
        references: list[ObjectRef] = []
        while True:
            request: dict[str, object] = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation is not None:
                request["ContinuationToken"] = continuation
            try:
                response = self._client.list_objects_v2(**request)
            except Exception as error:
                raise ObjectIntegrityError("S3 object discovery failed") from error
            contents = response.get("Contents", [])
            if not isinstance(contents, list):
                raise ObjectIntegrityError("S3 object listing is invalid")
            for entry in contents:
                if not isinstance(entry, Mapping) or not isinstance(
                    entry.get("Key"), str
                ):
                    raise ObjectIntegrityError("S3 object listing is invalid")
                key = entry["Key"]
                try:
                    head = self._client.head_object(
                        Bucket=self._bucket, Key=key, ChecksumMode="ENABLED"
                    )
                except Exception as error:
                    raise self._read_error(error) from error
                reference = self._reference_from_head(tenant_id, project_id, key, head)
                if reference.name.startswith(name_prefix):
                    references.append(reference)
            if response.get("IsTruncated") is not True:
                break
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                raise ObjectIntegrityError("S3 object listing continuation is invalid")
            continuation = next_token
        return tuple(sorted(references, key=lambda reference: reference.storage_key))

    @staticmethod
    def _reference(
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ) -> ObjectRef:
        _validate_uuid7(tenant_id, field="tenant_id")
        _validate_uuid7(project_id, field="project_id")
        _validate_name(name)
        _validate_media_type(media_type)
        if not isinstance(data, bytes):
            raise ObjectValidationError("data must be bytes")
        digest = sha256(data).hexdigest()
        return ObjectRef(
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            media_type=media_type,
            byte_count=len(data),
            sha256=digest,
            storage_key=storage_key_for(tenant_id, project_id, digest),
        )

    @staticmethod
    def _validate_scope(
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> None:
        _validate_uuid7(tenant_id, field="tenant_id")
        _validate_uuid7(project_id, field="project_id")
        if not isinstance(reference, ObjectRef):
            raise ObjectValidationError("reference must be an ObjectRef")
        if reference.tenant_id != tenant_id or reference.project_id != project_id:
            raise ObjectScopeError("object reference is outside caller scope")

    @staticmethod
    def _metadata(reference: ObjectRef) -> dict[str, str]:
        return {
            "schema": _S3_OBJECT_SCHEMA,
            "schema-version": _S3_OBJECT_SCHEMA_VERSION,
            "tenant-id": str(reference.tenant_id),
            "project-id": str(reference.project_id),
            "name": reference.name,
            "media-type": reference.media_type,
            "byte-count": str(reference.byte_count),
            "sha256": reference.sha256,
            "storage-key": reference.storage_key,
        }

    @staticmethod
    def _checksum(digest: str) -> str:
        return b64encode(bytes.fromhex(digest)).decode("ascii")

    def _verify_head(self, reference: ObjectRef) -> Mapping[str, object]:
        try:
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=reference.storage_key,
                ChecksumMode="ENABLED",
            )
        except Exception as error:
            raise self._read_error(error) from error
        self._verify_response_metadata(response, reference)
        return response

    @classmethod
    def _verify_response_metadata(
        cls,
        response: Mapping[str, object],
        reference: ObjectRef,
    ) -> None:
        metadata = response.get("Metadata")
        if not isinstance(metadata, Mapping) or dict(metadata) != cls._metadata(
            reference
        ):
            raise ObjectIntegrityError("S3 object metadata does not match reference")
        if response.get("ContentLength") != reference.byte_count:
            raise ObjectIntegrityError("S3 object length does not match reference")
        if response.get("ChecksumSHA256") != cls._checksum(reference.sha256):
            raise ObjectIntegrityError("S3 object checksum does not match reference")

    @classmethod
    def _reference_from_head(
        cls,
        tenant_id: UUID,
        project_id: UUID,
        storage_key: str,
        response: Mapping[str, object],
    ) -> ObjectRef:
        metadata = response.get("Metadata")
        if not isinstance(metadata, Mapping):
            raise ObjectIntegrityError("S3 object metadata is invalid")
        try:
            reference = ObjectRef(
                tenant_id=UUID(str(metadata["tenant-id"])),
                project_id=UUID(str(metadata["project-id"])),
                name=str(metadata["name"]),
                media_type=str(metadata["media-type"]),
                byte_count=int(str(metadata["byte-count"])),
                sha256=str(metadata["sha256"]),
                storage_key=str(metadata["storage-key"]),
            )
        except (KeyError, ValueError) as error:
            raise ObjectIntegrityError("S3 object metadata is invalid") from error
        if reference.tenant_id != tenant_id or reference.project_id != project_id:
            raise ObjectIntegrityError("S3 object metadata scope is invalid")
        if reference.storage_key != storage_key:
            raise ObjectIntegrityError("S3 object metadata key is invalid")
        cls._verify_response_metadata(response, reference)
        return reference

    @staticmethod
    def _version_id(response: Mapping[str, object]) -> str | None:
        version_id = response.get("VersionId")
        if not isinstance(version_id, str) or not version_id or version_id == "null":
            return None
        return version_id

    @staticmethod
    def _is_precondition_failure(error: Exception) -> bool:
        return S3ObjectStore._error_code(error) in {"412", "PreconditionFailed"}

    @staticmethod
    def _read_error(error: Exception) -> ObjectStorageError:
        if S3ObjectStore._error_code(error) in {"404", "NoSuchKey", "NotFound"}:
            return ObjectNotFound("S3 object is missing")
        return ObjectIntegrityError("S3 object could not be read")

    @staticmethod
    def _error_code(error: Exception) -> str | None:
        response = getattr(error, "response", None)
        if not isinstance(response, Mapping):
            return None
        details = response.get("Error")
        if not isinstance(details, Mapping):
            return None
        code = details.get("Code")
        return str(code) if code is not None else None


class FilesystemObjectStore:
    """Atomic local object store with tenant-safe content-addressed keys."""

    def __init__(self, root: Path) -> None:
        self._root = root
        if self._root.is_symlink():
            raise ObjectIntegrityError("object-store root must not be a symlink")
        self._root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ) -> ObjectRef:
        """Atomically create or verify one scoped content-addressed object."""
        _validate_uuid7(tenant_id, field="tenant_id")
        _validate_uuid7(project_id, field="project_id")
        _validate_name(name)
        _validate_media_type(media_type)
        if not isinstance(data, bytes):
            raise ObjectValidationError("data must be bytes")

        digest = sha256(data).hexdigest()
        storage_key = storage_key_for(tenant_id, project_id, digest)
        reference = ObjectRef(
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            media_type=media_type,
            byte_count=len(data),
            sha256=digest,
            storage_key=storage_key,
        )
        key_parts = tuple(storage_key.split("/"))
        parent_fd = self._open_directory_chain(key_parts[:-1], create=True)
        try:
            self._ensure_file(
                parent_fd,
                key_parts[-1],
                data,
                digest,
                len(data),
            )
            metadata_name = self._metadata_name(digest)
            expected_metadata = self._metadata_bytes(reference)
            try:
                metadata_fd = self._open_file(parent_fd, metadata_name)
            except ObjectNotFound:
                self._create_once(
                    parent_fd,
                    metadata_name,
                    expected_metadata,
                    sha256(expected_metadata).hexdigest(),
                    len(expected_metadata),
                )
                metadata_fd = self._open_file(parent_fd, metadata_name)
                try:
                    self._verify_metadata(metadata_fd, expected_metadata)
                finally:
                    os.close(metadata_fd)
            else:
                try:
                    self._verify_metadata(metadata_fd, expected_metadata)
                finally:
                    os.close(metadata_fd)
        finally:
            os.close(parent_fd)
        return reference

    def read(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> bytes:
        """Read and verify exact bytes after enforcing caller scope."""
        _validate_uuid7(tenant_id, field="tenant_id")
        _validate_uuid7(project_id, field="project_id")
        if not isinstance(reference, ObjectRef):
            raise ObjectValidationError("reference must be an ObjectRef")
        if reference.tenant_id != tenant_id or reference.project_id != project_id:
            raise ObjectScopeError("object reference is outside caller scope")
        key_parts = tuple(
            storage_key_for(
                reference.tenant_id, reference.project_id, reference.sha256
            ).split("/")
        )
        parent_fd = self._open_directory_chain(key_parts[:-1], create=False)
        try:
            expected_metadata = self._metadata_bytes(reference)
            try:
                metadata_fd = self._open_file(
                    parent_fd,
                    self._metadata_name(reference.sha256),
                )
            except ObjectNotFound as error:
                raise ObjectIntegrityError(
                    "persisted object metadata is missing"
                ) from error
            try:
                self._verify_metadata(metadata_fd, expected_metadata)
            finally:
                os.close(metadata_fd)
            object_fd = self._open_file(parent_fd, key_parts[-1])
            try:
                return self._read_verified(
                    object_fd,
                    reference.sha256,
                    reference.byte_count,
                )
            finally:
                os.close(object_fd)
        finally:
            os.close(parent_fd)

    def delete(
        self,
        tenant_id: UUID,
        project_id: UUID,
        reference: ObjectRef,
    ) -> None:
        """Verify and remove one scoped object and its exact metadata sidecar."""
        _validate_uuid7(tenant_id, field="tenant_id")
        _validate_uuid7(project_id, field="project_id")
        if not isinstance(reference, ObjectRef):
            raise ObjectValidationError("reference must be an ObjectRef")
        if reference.tenant_id != tenant_id or reference.project_id != project_id:
            raise ObjectScopeError("object reference is outside caller scope")
        self.read(tenant_id, project_id, reference)
        key_parts = tuple(
            storage_key_for(
                reference.tenant_id, reference.project_id, reference.sha256
            ).split("/")
        )
        parent_fd = self._open_directory_chain(key_parts[:-1], create=False)
        try:
            try:
                os.unlink(key_parts[-1], dir_fd=parent_fd)
                os.unlink(self._metadata_name(reference.sha256), dir_fd=parent_fd)
            except FileNotFoundError as error:
                raise ObjectNotFound("object or metadata is missing") from error
            except OSError as error:
                raise ObjectIntegrityError("object deletion failed") from error
        finally:
            os.close(parent_fd)
        for part_count in range(len(key_parts) - 1, 2, -1):
            directory = self._root.joinpath(*key_parts[:part_count])
            with suppress(OSError):
                directory.rmdir()

    def list(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name_prefix: str,
    ) -> tuple[ObjectRef, ...]:
        """Discover trusted scoped metadata without following filesystem links."""
        _validate_uuid7(tenant_id, field="tenant_id")
        _validate_uuid7(project_id, field="project_id")
        _validate_discovery_prefix(name_prefix)
        root_parts = (
            "tenants",
            str(tenant_id),
            "projects",
            str(project_id),
            "objects",
        )
        try:
            objects_fd = self._open_directory_chain(root_parts, create=False)
        except ObjectNotFound:
            return ()
        references: list[ObjectRef] = []
        try:
            for digest_prefix in os.listdir(objects_fd):
                if re.fullmatch(r"[0-9a-f]{2}", digest_prefix) is None:
                    continue
                try:
                    directory_fd = os.open(
                        digest_prefix, _DIRECTORY_FLAGS, dir_fd=objects_fd
                    )
                except OSError as error:
                    raise ObjectIntegrityError(
                        "object directory is not a safe directory"
                    ) from error
                try:
                    for metadata_name in os.listdir(directory_fd):
                        if not (
                            metadata_name.startswith(".")
                            and metadata_name.endswith(_METADATA_SUFFIX)
                        ):
                            continue
                        metadata_fd = self._open_file(directory_fd, metadata_name)
                        try:
                            reference = self._reference_from_metadata(
                                self._read_fd(metadata_fd)
                            )
                        finally:
                            os.close(metadata_fd)
                        if (
                            reference.tenant_id == tenant_id
                            and reference.project_id == project_id
                            and reference.name.startswith(name_prefix)
                        ):
                            references.append(reference)
                finally:
                    os.close(directory_fd)
        finally:
            os.close(objects_fd)
        return tuple(sorted(references, key=lambda reference: reference.storage_key))

    @staticmethod
    def _metadata_name(digest: str) -> str:
        """Return the sidecar name for one content-addressed object."""
        validate_sha256(digest)
        return f".{digest}{_METADATA_SUFFIX}"

    @staticmethod
    def _metadata_bytes(reference: ObjectRef) -> bytes:
        """Serialize reference metadata deterministically for the sidecar."""
        return json.dumps(
            reference.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _reference_from_metadata(cls, data: bytes) -> ObjectRef:
        """Parse and require canonical immutable metadata discovered on disk."""
        try:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError("metadata is not an object")
            reference = ObjectRef(
                tenant_id=UUID(str(payload["tenant_id"])),
                project_id=UUID(str(payload["project_id"])),
                name=str(payload["name"]),
                media_type=str(payload["media_type"]),
                byte_count=payload["byte_count"],
                sha256=str(payload["sha256"]),
                storage_key=str(payload["storage_key"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ObjectIntegrityError(
                "persisted object metadata is invalid"
            ) from error
        if cls._metadata_bytes(reference) != data:
            raise ObjectIntegrityError("persisted object metadata is not canonical")
        return reference

    def _open_directory_chain(
        self,
        parts: tuple[str, ...],
        *,
        create: bool,
    ) -> int:
        """Open canonical key directories without following ancestor symlinks."""
        try:
            current_fd = os.open(self._root, _DIRECTORY_FLAGS)
        except FileNotFoundError as error:
            raise ObjectNotFound("object-store root is missing") from error
        except OSError as error:
            raise ObjectIntegrityError(
                "object-store root is not a safe directory"
            ) from error

        try:
            for part in parts:
                try:
                    next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
                except FileNotFoundError as error:
                    if not create:
                        raise ObjectNotFound("object directory is missing") from error
                    with suppress(FileExistsError):
                        os.mkdir(part, 0o700, dir_fd=current_fd)
                    try:
                        next_fd = os.open(
                            part,
                            _DIRECTORY_FLAGS,
                            dir_fd=current_fd,
                        )
                    except FileNotFoundError as open_error:
                        raise ObjectNotFound(
                            "object directory is missing"
                        ) from open_error
                    except OSError as open_error:
                        raise ObjectIntegrityError(
                            "object directory is not a safe directory"
                        ) from open_error
                except OSError as error:
                    raise ObjectIntegrityError(
                        "object directory is not a safe directory"
                    ) from error
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except BaseException:
            with suppress(OSError):
                os.close(current_fd)
            raise

    @staticmethod
    def _open_file(parent_fd: int, name: str) -> int:
        """Open one regular file relative to a securely opened directory."""
        try:
            file_fd = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError as error:
            raise ObjectNotFound("object is missing") from error
        except OSError as error:
            raise ObjectIntegrityError("object path is not a safe file") from error
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ObjectIntegrityError("object path must be a regular file")
        except BaseException:
            with suppress(OSError):
                os.close(file_fd)
            raise
        return file_fd

    @staticmethod
    def _read_fd(file_fd: int) -> bytes:
        """Read bytes from one already-open file descriptor."""
        chunks: list[bytes] = []
        try:
            while chunk := os.read(file_fd, 1024 * 1024):
                chunks.append(chunk)
        except OSError as error:
            raise ObjectIntegrityError("object bytes could not be read") from error
        return b"".join(chunks)

    @classmethod
    def _read_verified(cls, file_fd: int, digest: str, byte_count: int) -> bytes:
        """Verify and return bytes read from the same descriptor."""
        data = cls._read_fd(file_fd)
        if len(data) != byte_count or sha256(data).hexdigest() != digest:
            raise ObjectIntegrityError("object bytes do not match reference")
        return data

    @classmethod
    def _verify_metadata(cls, file_fd: int, expected: bytes) -> None:
        """Require exact persisted reference metadata."""
        if cls._read_fd(file_fd) != expected:
            raise ObjectIntegrityError("persisted object metadata does not match")

    def _ensure_file(
        self,
        parent_fd: int,
        name: str,
        data: bytes,
        digest: str,
        byte_count: int,
    ) -> bool:
        """Create one file once, or verify the existing immutable file."""
        try:
            file_fd = self._open_file(parent_fd, name)
        except ObjectNotFound:
            return self._create_once(
                parent_fd,
                name,
                data,
                digest,
                byte_count,
            )
        try:
            self._read_verified(file_fd, digest, byte_count)
        finally:
            os.close(file_fd)
        return False

    @staticmethod
    def _create_once(
        parent_fd: int,
        name: str,
        data: bytes,
        digest: str,
        byte_count: int,
    ) -> bool:
        """Use a same-directory temporary and no-replace hard-link commit."""
        temporary_name: str | None = None
        temporary_fd = -1
        for _ in range(16):
            candidate = f".object-{uuid4().hex}"
            try:
                temporary_fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_name is None:
            raise ObjectStorageError("could not allocate an object temporary")

        try:
            with os.fdopen(temporary_fd, "wb") as temporary:
                temporary_fd = -1
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                try:
                    os.fsync(parent_fd)
                except OSError as error:
                    raise ObjectIntegrityError(
                        "object directory sync failed"
                    ) from error
                return True
            except FileExistsError:
                existing_fd = FilesystemObjectStore._open_file(parent_fd, name)
                try:
                    FilesystemObjectStore._read_verified(
                        existing_fd,
                        digest,
                        byte_count,
                    )
                finally:
                    os.close(existing_fd)
                return False
            except OSError as error:
                raise ObjectIntegrityError("object commit failed") from error
        finally:
            if temporary_fd >= 0:
                with suppress(OSError):
                    os.close(temporary_fd)
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=parent_fd)


__all__ = [
    "FilesystemObjectStore",
    "ObjectIntegrityError",
    "ObjectNotFound",
    "ObjectRef",
    "ObjectScopeError",
    "ObjectStorageError",
    "ObjectStoreDiscovery",
    "ObjectStore",
    "ObjectValidationError",
    "S3ObjectStore",
    "storage_key_for",
    "validate_sha256",
]
