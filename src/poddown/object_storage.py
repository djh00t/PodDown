"""Tenant-scoped content-addressed object storage ports and local adapter."""

from __future__ import annotations

import json
import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_NAME_LENGTH = 255
_MAX_MEDIA_TYPE_LENGTH = 255
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | _O_NOFOLLOW
_METADATA_SUFFIX = ".metadata.json"


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
            object_created = self._ensure_file(
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
                if not object_created:
                    raise ObjectIntegrityError(
                        "persisted object metadata is missing"
                    ) from None
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
    "ObjectStore",
    "ObjectValidationError",
    "storage_key_for",
    "validate_sha256",
]
