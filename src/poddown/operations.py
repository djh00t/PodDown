"""Fail-closed retention, export, and recovery evidence contracts.

These contracts deliberately stop at verified decisions and manifests.  They do
not perform tenant deletion or claim that a hosted database/object-store
restore has run; callers must supply the authorized mutation or restore
transport after the evidence has been verified.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_SCHEMA = "1.0.0"


class RetentionClass(StrEnum):
    """Data classes with independent retention policy windows."""

    SOURCE = "source"
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    PROVIDER_PAYLOAD = "provider_payload"
    LOG = "log"
    CONSENT_EVIDENCE = "consent_evidence"
    PUBLICATION_EVIDENCE = "publication_evidence"


class RetentionAction(StrEnum):
    """Decision returned before any destructive retention mutation."""

    RETAIN = "retain"
    DELETE_ELIGIBLE = "delete_eligible"
    BLOCKED = "blocked"


class ManifestKind(StrEnum):
    """The two immutable lifecycle evidence bundles supported locally."""

    EXPORT = "tenant-export"
    BACKUP = "backup"


class ManifestIntegrityError(ValueError):
    """A manifest or supplied restore/export bytes are not exact."""


class ArtifactScopeError(ValueError):
    """An artifact belongs to a different tenant or project scope."""


def _require_uuid7(value: UUID, *, field: str) -> UUID:
    if not isinstance(value, UUID) or value.version != 7:
        raise ValueError(f"{field} must be a UUIDv7")
    return value


def _require_timestamp(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _require_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_relative_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("/", "\\"))
        or "\x00" in value
        or "\\" in value
    ):
        raise ValueError("artifact name contains invalid path syntax")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("artifact name contains invalid path syntax")
    if len(value) > 512:
        raise ValueError("artifact name is too long")
    return value


def _require_media_type(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.count("/") != 1
        or any(character.isspace() for character in value)
    ):
        raise ValueError("artifact media type is invalid")
    return value


def _canonical_json(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ManifestIntegrityError("manifest fields are not JSON-safe") from error


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Independent retention windows expressed in whole days.

    ``None`` means retain indefinitely.  No default policy is supplied because
    retention is a product/legal decision, not an implementation default.
    """

    days: Mapping[RetentionClass, int | None]

    def __post_init__(self) -> None:
        expected = set(RetentionClass)
        normalized: dict[RetentionClass, int | None] = {}
        for raw_class, value in self.days.items():
            try:
                data_class = (
                    raw_class
                    if isinstance(raw_class, RetentionClass)
                    else RetentionClass(raw_class)
                )
            except (TypeError, ValueError) as error:
                raise ValueError("retention class is unsupported") from error
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("retention days must be non-negative integers")
            normalized[data_class] = value
        if set(normalized) != expected:
            raise ValueError("retention policy must define all retention classes")
        object.__setattr__(self, "days", MappingProxyType(normalized))

    @classmethod
    def from_days(
        cls,
        values: Mapping[RetentionClass | str, int | None],
    ) -> RetentionPolicy:
        """Construct a policy while preserving the explicit class mapping."""
        return cls(cast(Mapping[RetentionClass, int | None], values))


@dataclass(frozen=True, slots=True)
class DeletionAuthorization:
    """Explicit authorization required for immutable evidence deletion."""

    authorization_id: str
    approved_by: str
    reason: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.authorization_id, self.approved_by, self.reason)
        ):
            raise ValueError("deletion authorization fields are required")


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    """Immutable decision and audit reason for one retention evaluation."""

    data_class: RetentionClass
    action: RetentionAction
    captured_at: datetime
    expires_at: datetime | None
    reason: str
    authorization_id: str | None = None


class RetentionEvaluator:
    """Evaluate retention without performing deletion side effects."""

    def __init__(self, policy: RetentionPolicy) -> None:
        self._policy = policy

    def evaluate(
        self,
        *,
        data_class: RetentionClass,
        captured_at: datetime,
        now: datetime,
        immutable: bool = False,
        legal_hold: bool = False,
        authorization: DeletionAuthorization | None = None,
    ) -> RetentionDecision:
        """Return a fail-closed retention action and its audit explanation."""
        try:
            normalized_class = (
                data_class
                if isinstance(data_class, RetentionClass)
                else RetentionClass(data_class)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("retention class is unsupported") from error
        captured = _require_timestamp(captured_at, field="captured_at")
        current = _require_timestamp(now, field="now")
        if current < captured:
            raise ValueError("now must not precede captured_at")
        days = self._policy.days[normalized_class]
        expires = None if days is None else captured + timedelta(days=days)
        if legal_hold:
            return RetentionDecision(
                normalized_class,
                RetentionAction.BLOCKED,
                captured,
                expires,
                "legal hold blocks deletion",
                None if authorization is None else authorization.authorization_id,
            )
        if expires is None or current < expires:
            return RetentionDecision(
                normalized_class,
                RetentionAction.RETAIN,
                captured,
                expires,
                "retention window is active",
                None if authorization is None else authorization.authorization_id,
            )
        if immutable and authorization is None:
            return RetentionDecision(
                normalized_class,
                RetentionAction.BLOCKED,
                captured,
                expires,
                "immutable evidence requires explicit deletion authorization",
            )
        authorization_id = (
            None if authorization is None else authorization.authorization_id
        )
        reason = "retention window expired"
        if authorization_id is not None:
            reason = f"retention window expired under authorization {authorization_id}"
        return RetentionDecision(
            normalized_class,
            RetentionAction.DELETE_ELIGIBLE,
            captured,
            expires,
            reason,
            authorization_id,
        )


@dataclass(frozen=True, slots=True)
class LifecycleArtifact:
    """One tenant/project-scoped byte snapshot supplied to a manifest."""

    tenant_id: UUID
    project_id: UUID
    name: str
    media_type: str
    data: bytes

    def __post_init__(self) -> None:
        _require_uuid7(self.tenant_id, field="tenant_id")
        _require_uuid7(self.project_id, field="project_id")
        _require_relative_name(self.name)
        _require_media_type(self.media_type)
        if not isinstance(self.data, bytes):
            raise ValueError("artifact data must be bytes")

    @property
    def sha256(self) -> str:
        """Return the exact digest bound into a lifecycle manifest."""
        return sha256(self.data).hexdigest()


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """Immutable metadata for one exported or backed-up artifact."""

    name: str
    media_type: str
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        _require_relative_name(self.name)
        _require_media_type(self.media_type)
        if type(self.byte_count) is not int or self.byte_count < 0:
            raise ValueError("manifest byte_count must be non-negative")
        _require_sha256(self.sha256, field="manifest sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "media_type": self.media_type,
            "bytes": self.byte_count,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ManifestEntry:
        if set(value) != {"name", "media_type", "bytes", "sha256"}:
            raise ManifestIntegrityError("manifest entry fields are malformed")
        try:
            return cls(
                name=value["name"],  # type: ignore[arg-type]
                media_type=value["media_type"],  # type: ignore[arg-type]
                byte_count=value["bytes"],  # type: ignore[arg-type]
                sha256=value["sha256"],  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ManifestIntegrityError("manifest entry is malformed") from error


@dataclass(frozen=True, slots=True)
class LifecycleManifest:
    """Deterministic, exact-byte export or backup manifest."""

    kind: ManifestKind
    tenant_id: UUID
    project_id: UUID
    created_at: datetime
    entries: tuple[ManifestEntry, ...]
    manifest_sha256: str
    schema_version: str = _MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        try:
            kind = (
                self.kind
                if isinstance(self.kind, ManifestKind)
                else ManifestKind(self.kind)
            )
        except (TypeError, ValueError) as error:
            raise ManifestIntegrityError("manifest kind is unsupported") from error
        _require_uuid7(self.tenant_id, field="tenant_id")
        _require_uuid7(self.project_id, field="project_id")
        created_at = _require_timestamp(self.created_at, field="created_at")
        if self.schema_version != _MANIFEST_SCHEMA:
            raise ManifestIntegrityError("manifest schema version is unsupported")
        if not self.entries:
            raise ManifestIntegrityError("manifest must contain an artifact")
        if len({entry.name for entry in self.entries}) != len(self.entries):
            raise ManifestIntegrityError("manifest contains duplicate artifact names")
        _require_sha256(self.manifest_sha256, field="manifest sha256")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(
            self, "entries", tuple(sorted(self.entries, key=lambda item: item.name))
        )
        expected = self._digest_for_payload(self._payload())
        if self.manifest_sha256 != expected:
            raise ManifestIntegrityError("manifest checksum does not match contents")

    @classmethod
    def capture(
        cls,
        *,
        kind: ManifestKind,
        tenant_id: UUID,
        project_id: UUID,
        artifacts: Sequence[LifecycleArtifact],
        created_at: datetime,
    ) -> LifecycleManifest:
        """Capture exact bytes into a deterministic export/backup manifest."""
        if not artifacts:
            raise ValueError("manifest requires at least one artifact")
        _require_uuid7(tenant_id, field="tenant_id")
        _require_uuid7(project_id, field="project_id")
        entries: list[ManifestEntry] = []
        for artifact in artifacts:
            if artifact.tenant_id != tenant_id or artifact.project_id != project_id:
                raise ArtifactScopeError("artifact is outside manifest scope")
            entries.append(
                ManifestEntry(
                    name=artifact.name,
                    media_type=artifact.media_type,
                    byte_count=len(artifact.data),
                    sha256=artifact.sha256,
                )
            )
        normalized_created_at = _require_timestamp(created_at, field="created_at")
        candidate = {
            "schema_version": _MANIFEST_SCHEMA,
            "kind": ManifestKind(kind).value,
            "tenant_id": str(tenant_id),
            "project_id": str(project_id),
            "created_at": normalized_created_at.isoformat(),
            "entries": [
                entry.to_dict() for entry in sorted(entries, key=lambda item: item.name)
            ],
        }
        return cls(
            kind=ManifestKind(kind),
            tenant_id=tenant_id,
            project_id=project_id,
            created_at=normalized_created_at,
            entries=tuple(entries),
            manifest_sha256=cls._digest_for_payload(candidate),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> LifecycleManifest:
        """Parse one manifest and recheck its self-digest."""
        required = {
            "schema_version",
            "kind",
            "tenant_id",
            "project_id",
            "created_at",
            "entries",
            "manifest_sha256",
        }
        if set(value) != required:
            raise ManifestIntegrityError("manifest fields are malformed")
        try:
            raw_entries = value["entries"]
            if not isinstance(raw_entries, list):
                raise ManifestIntegrityError("manifest entries are malformed")
            entries = tuple(
                ManifestEntry.from_dict(item)
                for item in raw_entries
                if isinstance(item, Mapping)
            )
            if len(entries) != len(raw_entries):
                raise ManifestIntegrityError("manifest entries are malformed")
            return cls(
                kind=value["kind"],  # type: ignore[arg-type]
                tenant_id=UUID(value["tenant_id"]),  # type: ignore[arg-type]
                project_id=UUID(value["project_id"]),  # type: ignore[arg-type]
                created_at=datetime.fromisoformat(value["created_at"]),  # type: ignore[arg-type]
                entries=entries,
                manifest_sha256=value["manifest_sha256"],  # type: ignore[arg-type]
                schema_version=value["schema_version"],  # type: ignore[arg-type]
            )
        except ManifestIntegrityError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ManifestIntegrityError("manifest is malformed") from error

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "created_at": self.created_at.isoformat(),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @staticmethod
    def _digest_for_payload(payload: Mapping[str, object]) -> str:
        return sha256(_canonical_json(payload)).hexdigest()

    def verify(self, artifacts: Sequence[LifecycleArtifact]) -> None:
        """Verify scope, names, media, byte counts, and hashes for restore/export."""
        if len(artifacts) != len(self.entries):
            raise ManifestIntegrityError("artifact count does not match manifest")
        by_name: dict[str, LifecycleArtifact] = {}
        for artifact in artifacts:
            if (
                artifact.tenant_id != self.tenant_id
                or artifact.project_id != self.project_id
            ):
                raise ArtifactScopeError("artifact is outside manifest scope")
            if artifact.name in by_name:
                raise ManifestIntegrityError("artifact names are duplicated")
            by_name[artifact.name] = artifact
        expected = {entry.name: entry for entry in self.entries}
        if set(by_name) != set(expected):
            raise ManifestIntegrityError("artifact names do not match manifest")
        for name, entry in expected.items():
            artifact = by_name[name]
            if (
                artifact.media_type != entry.media_type
                or len(artifact.data) != entry.byte_count
                or artifact.sha256 != entry.sha256
            ):
                raise ManifestIntegrityError(f"artifact integrity failed for {name}")

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe manifest without artifact bytes."""
        return {**self._payload(), "manifest_sha256": self.manifest_sha256}


class FilesystemLifecycleArchive:
    """Materialize and restore one exact local lifecycle archive.

    This adapter is intentionally filesystem-only evidence. It validates the
    manifest before writing, accepts identical replay, rejects mismatched or
    unexpected entries, and never overwrites existing bytes.
    """

    _MANIFEST_NAME = "manifest.json"
    _ARTIFACTS_DIR = "artifacts"

    def __init__(self, root: str | Path) -> None:
        try:
            self._root = Path(root)
        except TypeError as error:
            raise TypeError("archive root must be path-like") from error

    def write(
        self,
        manifest: LifecycleManifest,
        artifacts: Sequence[LifecycleArtifact],
    ) -> None:
        """Write an exact manifest and scoped artifact bytes idempotently."""
        if not isinstance(manifest, LifecycleManifest):
            raise TypeError("manifest must be LifecycleManifest")
        artifact_values = tuple(artifacts)
        if not all(isinstance(item, LifecycleArtifact) for item in artifact_values):
            raise TypeError("artifacts must contain LifecycleArtifact values")
        manifest.verify(artifact_values)
        self._ensure_root()
        existing = self._read_manifest(required=False)
        if existing is not None and existing != manifest:
            raise ManifestIntegrityError("archive manifest does not match")
        self._validate_layout()
        expected_names = {entry.name for entry in manifest.entries}
        existing_names = self._inventory_artifacts()
        unexpected = existing_names - expected_names
        if unexpected:
            raise ManifestIntegrityError("archive contains unexpected artifacts")
        for artifact in artifact_values:
            self._write_exact(self._artifact_path(artifact.name), artifact.data)
        if self._inventory_artifacts() != expected_names:
            raise ManifestIntegrityError("archive artifact set is incomplete")
        if existing is None:
            self._write_exact(
                self._root / self._MANIFEST_NAME,
                _canonical_json(manifest.to_dict()),
            )

    def restore(self) -> tuple[LifecycleManifest, tuple[LifecycleArtifact, ...]]:
        """Restore and verify all exact bytes from the local archive."""
        self._validate_layout()
        manifest = self._read_manifest(required=True)
        if manifest is None:  # pragma: no cover - required=True guarantees this
            raise ManifestIntegrityError("archive manifest is missing")
        expected_names = {entry.name for entry in manifest.entries}
        actual_names = self._inventory_artifacts()
        if actual_names != expected_names:
            raise ManifestIntegrityError("archive artifact set does not match manifest")
        artifacts = tuple(
            LifecycleArtifact(
                tenant_id=manifest.tenant_id,
                project_id=manifest.project_id,
                name=entry.name,
                media_type=entry.media_type,
                data=self._read_exact(self._artifact_path(entry.name)),
            )
            for entry in manifest.entries
        )
        try:
            manifest.verify(artifacts)
        except (ArtifactScopeError, ManifestIntegrityError):
            raise
        return manifest, artifacts

    def _ensure_root(self) -> None:
        if self._root.is_symlink() or (self._root.exists() and not self._root.is_dir()):
            raise ManifestIntegrityError("archive root is not a directory")
        self._root.mkdir(parents=True, exist_ok=True)

    def _validate_layout(self) -> None:
        if self._root.is_symlink() or not self._root.is_dir():
            raise ManifestIntegrityError("archive root is not a directory")
        allowed = {self._MANIFEST_NAME, self._ARTIFACTS_DIR}
        for child in self._root.iterdir():
            if child.is_symlink() or child.name not in allowed:
                raise ManifestIntegrityError("archive contains unexpected entries")
        artifact_root = self._root / self._ARTIFACTS_DIR
        if artifact_root.exists() and (
            artifact_root.is_symlink() or not artifact_root.is_dir()
        ):
            raise ManifestIntegrityError("archive artifacts entry is invalid")

    def _read_manifest(self, *, required: bool) -> LifecycleManifest | None:
        path = self._root / self._MANIFEST_NAME
        if not path.exists():
            if required:
                raise ManifestIntegrityError("archive manifest is missing")
            return None
        if path.is_symlink() or not path.is_file():
            raise ManifestIntegrityError("archive manifest entry is invalid")
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(decoded, Mapping):
                raise ManifestIntegrityError("archive manifest is malformed")
            return LifecycleManifest.from_dict(decoded)
        except ManifestIntegrityError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ManifestIntegrityError("archive manifest is malformed") from error

    def _inventory_artifacts(self) -> set[str]:
        artifact_root = self._root / self._ARTIFACTS_DIR
        if not artifact_root.exists():
            return set()
        names: set[str] = set()
        for path in artifact_root.rglob("*"):
            if path.is_symlink():
                raise ManifestIntegrityError("archive contains a symlink")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ManifestIntegrityError("archive contains an invalid artifact")
            names.add(path.relative_to(artifact_root).as_posix())
        return names

    def _artifact_path(self, name: str) -> Path:
        artifact_root = self._root / self._ARTIFACTS_DIR
        target = artifact_root / Path(name)
        try:
            target.resolve(strict=False).relative_to(
                artifact_root.resolve(strict=False)
            )
        except ValueError as error:
            raise ManifestIntegrityError(
                "artifact path escapes archive root"
            ) from error
        current = artifact_root
        for part in Path(name).parts:
            current /= part
            if current.is_symlink():
                raise ManifestIntegrityError("artifact path contains a symlink")
        return target

    @staticmethod
    def _read_exact(path: Path) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise ManifestIntegrityError("archive artifact entry is invalid")
        try:
            return path.read_bytes()
        except OSError as error:
            raise ManifestIntegrityError(
                "archive artifact could not be read"
            ) from error

    @staticmethod
    def _write_exact(path: Path, data: bytes) -> None:
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ManifestIntegrityError("archive entry is invalid")
            if FilesystemLifecycleArchive._read_exact(path) != data:
                raise ManifestIntegrityError("archive bytes do not match")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
            )
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
            temporary_name = None
        except OSError as error:
            raise ManifestIntegrityError(
                "archive bytes could not be written"
            ) from error
        finally:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name)


__all__ = [
    "ArtifactScopeError",
    "DeletionAuthorization",
    "FilesystemLifecycleArchive",
    "LifecycleArtifact",
    "LifecycleManifest",
    "ManifestEntry",
    "ManifestIntegrityError",
    "ManifestKind",
    "RetentionAction",
    "RetentionClass",
    "RetentionDecision",
    "RetentionEvaluator",
    "RetentionPolicy",
]
