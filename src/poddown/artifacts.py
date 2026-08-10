"""Content-addressed immutable artifact storage boundaries."""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ArtifactError(ValueError):
    """Base error for invalid or unavailable immutable artifacts."""


class ArtifactIntegrityError(ArtifactError, RuntimeError):
    """Raised when stored bytes do not match their immutable reference."""


def validate_artifact_name(name: str) -> None:
    """Reject names that could escape the package namespace or be ambiguous."""
    if not isinstance(name, str) or _ARTIFACT_NAME_PATTERN.fullmatch(name) is None:
        raise ArtifactError("artifact name is not a safe package member")


def validate_sha256(value: str, field_name: str = "SHA-256") -> None:
    """Require a lowercase hexadecimal SHA-256 digest."""
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ArtifactError(f"{field_name} must be a lowercase SHA-256 digest")


def storage_key_for_sha256(digest: str) -> str:
    """Return the deterministic relative object key for one content digest."""
    validate_sha256(digest)
    return f"objects/{digest[:2]}/{digest}"


def fsync_directory(directory: Path) -> None:
    """Persist a directory entry after an atomic immutable link."""
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise ArtifactError("immutable directory sync failed") from error
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class ArtifactRef:
    """Immutable package metadata bound to one exact stored byte sequence."""

    name: str
    media_type: str
    byte_count: int
    sha256: str
    storage_key: str


class ArtifactStore(Protocol):
    """Storage port for immutable content-addressed artifacts."""

    def put(self, name: str, media_type: str, data: bytes) -> ArtifactRef:
        """Store exact bytes once and return their immutable reference."""

    def read(self, reference: ArtifactRef) -> bytes:
        """Read and verify bytes for an immutable reference."""


class FilesystemArtifactStore:
    """Store immutable artifacts below a local content-addressed root."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, name: str, media_type: str, data: bytes) -> ArtifactRef:
        """Atomically create or reuse the object for exact bytes."""
        validate_artifact_name(name)
        if not isinstance(media_type, str) or not media_type:
            raise ArtifactError("artifact media type must be non-empty")
        if not isinstance(data, bytes):
            raise ArtifactError("artifact data must be bytes")

        digest = sha256(data).hexdigest()
        storage_key = storage_key_for_sha256(digest)
        reference = ArtifactRef(
            name=name,
            media_type=media_type,
            byte_count=len(data),
            sha256=digest,
            storage_key=storage_key,
        )
        target = self._root / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_existing(target, reference)
            return reference

        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent,
                prefix=".artifact-",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                self._verify_existing(target, reference)
            fsync_directory(target.parent)
            return reference
        except OSError as error:
            raise ArtifactError("immutable artifact write failed") from error
        finally:
            if temporary_path is not None:
                with suppress(FileNotFoundError):
                    temporary_path.unlink()

    def read(self, reference: ArtifactRef) -> bytes:
        """Read bytes only when path, size, and checksum all agree."""
        if not isinstance(reference, ArtifactRef):
            raise ArtifactIntegrityError("artifact reference is malformed")
        expected_key = storage_key_for_sha256(reference.sha256)
        if reference.storage_key != expected_key:
            raise ArtifactIntegrityError(
                "artifact storage key is not content-addressed"
            )
        try:
            data = (self._root / expected_key).read_bytes()
        except OSError as error:
            raise ArtifactIntegrityError("immutable artifact is unavailable") from error
        self._verify_existing(self._root / expected_key, reference)
        return data

    @staticmethod
    def _verify_existing(path: Path, reference: ArtifactRef) -> None:
        try:
            data = path.read_bytes()
        except OSError as error:
            raise ArtifactIntegrityError("immutable artifact is unavailable") from error
        if (
            len(data) != reference.byte_count
            or sha256(data).hexdigest() != reference.sha256
        ):
            raise ArtifactIntegrityError(
                f"immutable artifact integrity check failed for {path.name}"
            )


__all__ = [
    "ArtifactError",
    "ArtifactIntegrityError",
    "ArtifactRef",
    "ArtifactStore",
    "FilesystemArtifactStore",
    "fsync_directory",
    "storage_key_for_sha256",
    "validate_artifact_name",
    "validate_sha256",
]
