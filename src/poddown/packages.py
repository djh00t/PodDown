"""Immutable episode-package assembly and replay-safe manifest commits."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID

from poddown.artifacts import (
    ArtifactError,
    ArtifactRef,
    ArtifactStore,
    fsync_directory,
    storage_key_for_sha256,
    validate_artifact_name,
    validate_sha256,
)

REQUIRED_PACKAGE_ARTIFACTS = (
    "episode.wav",
    "episode.mp3",
    "transcript.txt",
    "transcript.vtt",
    "chapters.json",
    "show-notes.md",
    "qa-report.json",
    "provenance.json",
    "render-manifest.json",
)


class PackageError(ValueError):
    """Raised when a package cannot satisfy the immutable package contract."""


class PackageConflictError(PackageError, RuntimeError):
    """Raised when an episode version already has different package evidence."""


class PackageIntegrityError(PackageError, RuntimeError):
    """Raised when a stored package manifest is malformed or unreadable."""


@dataclass(frozen=True)
class PackageArtifact:
    """One named byte payload to be committed into an episode package."""

    name: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class PackageProvenance:
    """Required package provenance and extensible provider/workflow details."""

    source_sha256: str
    script_version: int
    profile_version: str
    renderer: str
    qa: str
    critical_token_accuracy: float
    final_sha256: str
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.details, Mapping):
            raise PackageError("package provenance details must be a mapping")
        try:
            details = json.loads(json.dumps(dict(self.details), allow_nan=False))
        except (TypeError, ValueError, OverflowError) as error:
            raise PackageError(
                "package provenance details must be JSON-compatible"
            ) from error
        if not isinstance(details, dict):
            raise PackageError("package provenance details must be a mapping")
        object.__setattr__(self, "details", MappingProxyType(details))

    def to_dict(self) -> dict[str, object]:
        """Return the schema-compatible provenance object."""
        return {
            "source_sha256": self.source_sha256,
            "script_version": self.script_version,
            "profile_version": self.profile_version,
            "renderer": self.renderer,
            "qa": self.qa,
            "critical_token_accuracy": self.critical_token_accuracy,
            "final_sha256": self.final_sha256,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PackageProvenance:
        """Reconstruct provenance from a persisted manifest mapping."""
        try:
            details = value.get("details", {})
            if not isinstance(details, Mapping):
                raise PackageIntegrityError("package provenance details are malformed")
            return cls(
                source_sha256=cast(str, value["source_sha256"]),
                script_version=cast(int, value["script_version"]),
                profile_version=cast(str, value["profile_version"]),
                renderer=cast(str, value["renderer"]),
                qa=cast(str, value["qa"]),
                critical_token_accuracy=cast(float, value["critical_token_accuracy"]),
                final_sha256=cast(str, value["final_sha256"]),
                details=cast(Mapping[str, object], details),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PackageIntegrityError("package provenance is malformed") from error


@dataclass(frozen=True)
class EpisodePackage:
    """Immutable package manifest with deterministic schema serialization."""

    episode_version_id: str
    files: tuple[ArtifactRef, ...]
    provenance: PackageProvenance
    schema_version: str = "1.0.0"

    def to_dict(self) -> dict[str, object]:
        """Return the approved manifest shape without storage-only fields."""
        return {
            "schema_version": self.schema_version,
            "episode_version_id": self.episode_version_id,
            "files": [
                {
                    "name": reference.name,
                    "media_type": reference.media_type,
                    "bytes": reference.byte_count,
                    "sha256": reference.sha256,
                }
                for reference in self.files
            ],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EpisodePackage:
        """Parse and validate one persisted episode-package manifest."""
        if set(value) != {
            "schema_version",
            "episode_version_id",
            "files",
            "provenance",
        }:
            raise PackageIntegrityError("package manifest fields are malformed")
        if value.get("schema_version") != "1.0.0":
            raise PackageIntegrityError("package schema version is unsupported")
        episode_version_id = _normalize_episode_version_id(
            value.get("episode_version_id")
        )
        raw_files = value.get("files")
        raw_provenance = value.get("provenance")
        if not isinstance(raw_files, list) or not isinstance(raw_provenance, Mapping):
            raise PackageIntegrityError("package manifest contents are malformed")
        references: list[ArtifactRef] = []
        try:
            for raw_file in raw_files:
                if not isinstance(raw_file, Mapping):
                    raise PackageIntegrityError("package file metadata is malformed")
                if set(raw_file) != {"name", "media_type", "bytes", "sha256"}:
                    raise PackageIntegrityError("package file fields are malformed")
                name = cast(str, raw_file["name"])
                media_type = cast(str, raw_file["media_type"])
                byte_count = cast(int, raw_file["bytes"])
                digest = cast(str, raw_file["sha256"])
                validate_artifact_name(name)
                if not isinstance(media_type, str) or not media_type:
                    raise PackageIntegrityError("package media type is malformed")
                if type(byte_count) is not int or byte_count < 0:
                    raise PackageIntegrityError("package byte count is malformed")
                validate_sha256(digest, "package file checksum")
                references.append(
                    ArtifactRef(
                        name=name,
                        media_type=media_type,
                        byte_count=byte_count,
                        sha256=digest,
                        storage_key=storage_key_for_sha256(digest),
                    )
                )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, PackageIntegrityError):
                raise
            raise PackageIntegrityError("package file metadata is malformed") from error
        package = cls(
            episode_version_id=episode_version_id,
            files=tuple(references),
            provenance=PackageProvenance.from_dict(raw_provenance),
        )
        _validate_manifest_contract(package)
        return package


def manifest_sha256_for(package: EpisodePackage) -> str:
    """Return the digest of the canonical immutable package manifest bytes."""
    if not isinstance(package, EpisodePackage):
        raise PackageError("package manifest must be an EpisodePackage")
    return sha256(_manifest_bytes(package)).hexdigest()


def package_sha256_for(package: EpisodePackage, artifact_store: ArtifactStore) -> str:
    """Return the canonical digest of every named package artifact's exact bytes.

    This digest is intentionally distinct from ``final_sha256`` (the WAV digest)
    and ``manifest_sha256_for`` (the manifest digest). The framing includes the
    package member name and media type so the result is stable and unambiguous.
    """
    if not isinstance(package, EpisodePackage):
        raise PackageError("package must be an EpisodePackage")
    _validate_manifest_contract(package)
    digest = sha256(b"poddown-package-bytes-v1\0")
    for reference in package.files:
        try:
            data = artifact_store.read(reference)
        except (ArtifactError, OSError) as error:
            raise PackageIntegrityError("package artifact cannot be read") from error
        if not isinstance(data, bytes):
            raise PackageIntegrityError("package artifact bytes are malformed")
        for value in (reference.name, reference.media_type):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _manifest_bytes(package: EpisodePackage) -> bytes:
    try:
        return json.dumps(
            package.to_dict(),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise PackageError("package manifest contains non-JSON evidence") from error


class EpisodePackageService:
    """Assemble and atomically commit immutable episode-package manifests."""

    def __init__(self, artifact_store: ArtifactStore, package_root: Path) -> None:
        self._artifact_store = artifact_store
        self._package_root = Path(package_root)
        self._package_root.mkdir(parents=True, exist_ok=True)

    def commit(
        self,
        episode_version_id: str | UUID,
        artifacts: Sequence[PackageArtifact],
        provenance: PackageProvenance,
    ) -> EpisodePackage:
        """Commit one package once, replaying identical evidence safely."""
        normalized_id = _normalize_episode_version_id(episode_version_id)
        normalized_artifacts = tuple(artifacts)
        _validate_artifacts(normalized_artifacts)
        _validate_provenance(provenance)
        references = tuple(
            _reference_for_artifact(artifact) for artifact in normalized_artifacts
        )
        ordered_references = _order_references(references)
        candidate = EpisodePackage(
            episode_version_id=normalized_id,
            files=ordered_references,
            provenance=provenance,
        )
        _validate_manifest_contract(candidate)

        existing = self.get(normalized_id)
        if existing is not None:
            if existing != candidate:
                raise PackageConflictError(
                    "episode version already has a different immutable package"
                )
            for reference in existing.files:
                self._artifact_store.read(reference)
            return existing

        for artifact, reference in zip(normalized_artifacts, references, strict=True):
            stored = self._artifact_store.put(
                artifact.name, artifact.media_type, artifact.data
            )
            if stored != reference:
                raise PackageIntegrityError(
                    "artifact store returned mismatched metadata"
                )
        return self._commit_manifest(candidate)

    def get(self, episode_version_id: str | UUID) -> EpisodePackage | None:
        """Read one committed manifest or return None when it is absent."""
        normalized_id = _normalize_episode_version_id(episode_version_id)
        path = self._package_root / f"{normalized_id}.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PackageIntegrityError("package manifest cannot be read") from error
        if not isinstance(value, Mapping):
            raise PackageIntegrityError("package manifest is not an object")
        return EpisodePackage.from_dict(value)

    def _commit_manifest(self, package: EpisodePackage) -> EpisodePackage:
        path = self._package_root / f"{package.episode_version_id}.json"
        payload = _manifest_bytes(package)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._package_root,
                prefix=".package-",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                existing = self.get(package.episode_version_id)
                if existing == package:
                    return existing
                raise PackageConflictError(
                    "episode version already has a different immutable package"
                ) from None
            fsync_directory(self._package_root)
            return package
        except PackageError:
            raise
        except OSError as error:
            raise PackageError("immutable package manifest write failed") from error
        finally:
            if temporary_path is not None:
                with suppress(FileNotFoundError):
                    temporary_path.unlink()


def _normalize_episode_version_id(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as error:
        raise PackageError("episode version ID must be a UUID") from error


def _validate_artifacts(artifacts: tuple[PackageArtifact, ...]) -> None:
    if not artifacts:
        raise PackageError("package requires artifacts")
    names: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, PackageArtifact):
            raise PackageError("package artifacts are malformed")
        validate_artifact_name(artifact.name)
        if not isinstance(artifact.media_type, str) or not artifact.media_type:
            raise PackageError("package artifact media type is malformed")
        if not isinstance(artifact.data, bytes):
            raise PackageError("package artifact data must be bytes")
        names.append(artifact.name)
    if len(names) != len(set(names)):
        raise PackageError("package artifact names must be unique")
    if not set(REQUIRED_PACKAGE_ARTIFACTS).issubset(names):
        raise PackageError("package is missing an approved required artifact")


def _validate_provenance(provenance: PackageProvenance) -> None:
    if not isinstance(provenance, PackageProvenance):
        raise PackageError("package provenance is malformed")
    validate_sha256(provenance.source_sha256, "source checksum")
    if type(provenance.script_version) is not int or provenance.script_version < 1:
        raise PackageError("script version must be a positive integer")
    for name, value in (
        ("profile version", provenance.profile_version),
        ("renderer", provenance.renderer),
    ):
        if not isinstance(value, str) or not value:
            raise PackageError(f"{name} must be non-empty")
    if provenance.qa != "pass":
        raise PackageError("package QA evidence must pass")
    if (
        isinstance(provenance.critical_token_accuracy, bool)
        or not isinstance(provenance.critical_token_accuracy, (int, float))
        or float(provenance.critical_token_accuracy) != 1.0
    ):
        raise PackageError("critical-token accuracy must be exactly 1.0")
    validate_sha256(provenance.final_sha256, "final checksum")
    try:
        json.dumps(dict(provenance.details), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise PackageError(
            "package provenance details are not JSON serializable"
        ) from error


def _reference_for_artifact(artifact: PackageArtifact) -> ArtifactRef:
    digest = sha256(artifact.data).hexdigest()
    return ArtifactRef(
        name=artifact.name,
        media_type=artifact.media_type,
        byte_count=len(artifact.data),
        sha256=digest,
        storage_key=storage_key_for_sha256(digest),
    )


def _order_references(references: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    rank = {name: index for index, name in enumerate(REQUIRED_PACKAGE_ARTIFACTS)}
    return tuple(
        sorted(
            references,
            key=lambda reference: (rank.get(reference.name, len(rank)), reference.name),
        )
    )


def _validate_manifest_contract(package: EpisodePackage) -> None:
    if package.schema_version != "1.0.0":
        raise PackageError("package schema version is unsupported")
    try:
        UUID(package.episode_version_id)
    except ValueError as error:
        raise PackageError("package episode version ID is malformed") from error
    if len(package.files) < len(REQUIRED_PACKAGE_ARTIFACTS):
        raise PackageError("package manifest has too few files")
    names = tuple(reference.name for reference in package.files)
    if len(names) != len(set(names)) or not set(REQUIRED_PACKAGE_ARTIFACTS).issubset(
        names
    ):
        raise PackageError("package manifest required files are incomplete")
    for reference in package.files:
        validate_artifact_name(reference.name)
        if not reference.media_type or reference.byte_count < 0:
            raise PackageError("package manifest file metadata is malformed")
        validate_sha256(reference.sha256, "package file checksum")
    _validate_provenance(package.provenance)
    wav = next(
        reference for reference in package.files if reference.name == "episode.wav"
    )
    if package.provenance.final_sha256 != wav.sha256:
        raise PackageError("final checksum does not match episode WAV")


__all__ = [
    "EpisodePackage",
    "EpisodePackageService",
    "manifest_sha256_for",
    "package_sha256_for",
    "PackageArtifact",
    "PackageConflictError",
    "PackageError",
    "PackageIntegrityError",
    "PackageProvenance",
    "REQUIRED_PACKAGE_ARTIFACTS",
]
