"""Tenant-scoped, idempotent episode lifecycle contracts for the API boundary."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from uuid6 import uuid7

from poddown.content.source import snapshot_source
from poddown.intake import validate_markdown

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

EpisodeIdFactory = Callable[[], UUID]
Clock = Callable[[], datetime]


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _new_episode_id() -> UUID:
    return uuid7()


def _require_uuid7(name: str, value: object) -> UUID:
    if not isinstance(value, UUID) or value.version != 7:
        raise ValueError(f"{name} must be a UUIDv7")
    return value


def _require_text(name: str, value: object, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return value


def _require_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _freeze_json(value: object) -> object:
    """Copy JSON-shaped evidence into immutable values without source text."""
    if type(value) is float and not math.isfinite(value):
        raise ValueError("evidence numbers must be finite")
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("evidence keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise ValueError(f"unsupported evidence value: {type(value).__name__}")


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


class EpisodeState(StrEnum):
    """Monotonic lifecycle states exposed by the episode service."""

    VALIDATED = "validated"
    SCRIPTED = "scripted"
    RENDERED = "rendered"
    QA_PASSED = "qa_passed"
    PACKAGED = "packaged"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(frozen=True)
class StructuredFailure:
    """Redacted, actionable failure evidence safe for API status responses."""

    code: str
    stage: str
    message: str
    retriable: bool
    details: Mapping[str, object]
    status: int = 409

    def __post_init__(self) -> None:
        try:
            code = _require_text("failure code", self.code, max_length=128)
            stage = _require_text("failure stage", self.stage, max_length=128)
            message = _require_text("failure message", self.message, max_length=512)
        except ValueError as error:
            raise ValueError(str(error)) from error
        if type(self.retriable) is not bool:
            raise ValueError("failure retriable must be a boolean")
        if type(self.status) is not int or not 400 <= self.status <= 599:
            raise ValueError("failure status must be an HTTP error status")
        frozen = _freeze_json(self.details)
        if not isinstance(frozen, Mapping):
            raise ValueError("failure details must be a mapping")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "details", frozen)

    def to_dict(self) -> dict[str, object]:
        """Return JSON-shaped failure evidence without mutable references."""
        return {
            "status": self.status,
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "retriable": self.retriable,
            "details": _json_value(self.details),
        }


class EpisodeServiceError(ValueError):
    """Base error carrying stable, redacted API problem details."""

    def __init__(self, failure: StructuredFailure) -> None:
        self.failure = failure
        super().__init__(failure.message)


class EpisodeValidationError(EpisodeServiceError):
    """The episode creation command is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(
            StructuredFailure(
                code="episode_validation_failed",
                stage="validation",
                message=message,
                retriable=False,
                details={},
                status=422,
            )
        )


class EpisodeNotFound(EpisodeServiceError):
    """The requested episode is absent from the caller's tenant scope."""

    def __init__(self) -> None:
        super().__init__(
            StructuredFailure(
                code="episode_not_found",
                stage="lookup",
                message="episode was not found",
                retriable=False,
                details={},
                status=404,
            )
        )


class IdempotencyConflict(EpisodeServiceError):
    """An idempotency key was reused for a different request fingerprint."""

    def __init__(self) -> None:
        super().__init__(
            StructuredFailure(
                code="idempotency_conflict",
                stage="creation",
                message="idempotency key is already bound to another request",
                retriable=False,
                details={},
            )
        )


class InvalidEpisodeTransition(EpisodeServiceError):
    """A lifecycle transition violates the explicit state contract."""

    def __init__(self, message: str = "episode transition is not allowed") -> None:
        super().__init__(
            StructuredFailure(
                code="invalid_episode_transition",
                stage="lifecycle",
                message=message,
                retriable=False,
                details={},
            )
        )


class PublishAuthorizationError(EpisodeServiceError):
    """Publication was requested without an explicit authorization decision."""

    def __init__(self) -> None:
        super().__init__(
            StructuredFailure(
                code="publish_not_authorized",
                stage="publishing",
                message="explicit publish authorization is required",
                retriable=False,
                details={},
                status=403,
            )
        )


class VersionConflict(EpisodeServiceError):
    """The caller attempted to update an older immutable episode version."""

    def __init__(self) -> None:
        super().__init__(
            StructuredFailure(
                code="episode_version_conflict",
                stage="lifecycle",
                message="episode version is stale",
                retriable=True,
                details={},
            )
        )


@dataclass(frozen=True)
class EpisodeCreateCommand:
    """Validated-at-service-boundary request data for one episode version."""

    tenant_id: UUID
    project_id: UUID
    idempotency_key: str
    source_bytes: bytes
    profile_name: str


@dataclass(frozen=True)
class EpisodeRecord:
    """Immutable tenant-scoped episode state without source or secret leakage."""

    tenant_id: UUID
    project_id: UUID
    episode_id: UUID
    idempotency_key: str
    profile_name: str
    source_sha256: str
    source_bytes: int
    request_fingerprint: str
    state: EpisodeState
    version: int
    created_at: datetime
    updated_at: datetime
    qa_evidence: Mapping[str, object] | None = None
    package_sha256: str | None = None
    package_manifest_sha256: str | None = None
    failure: StructuredFailure | None = None

    def __post_init__(self) -> None:
        for field_name, field_value in (
            ("tenant_id", self.tenant_id),
            ("project_id", self.project_id),
            ("episode_id", self.episode_id),
        ):
            _require_uuid7(field_name, field_value)
        _require_text("idempotency key", self.idempotency_key, max_length=255)
        profile_name = _require_text("profile name", self.profile_name, max_length=63)
        if _PROFILE_ID.fullmatch(profile_name) is None:
            raise ValueError("profile name has invalid format")
        _require_sha256("source_sha256", self.source_sha256)
        _require_sha256("request_fingerprint", self.request_fingerprint)
        if type(self.source_bytes) is not int or self.source_bytes < 1:
            raise ValueError("source_bytes must be a positive integer")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if self.state not in EpisodeState:
            raise ValueError("state must be an EpisodeState")
        for timestamp_name, timestamp_value in (
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            if timestamp_value.tzinfo is None or timestamp_value.utcoffset() is None:
                raise ValueError(f"{timestamp_name} must be timezone-aware")
        if self.qa_evidence is not None:
            frozen_qa = _freeze_json(self.qa_evidence)
            if not isinstance(frozen_qa, Mapping):
                raise ValueError("qa_evidence must be a mapping")
            object.__setattr__(self, "qa_evidence", frozen_qa)
        if self.package_sha256 is not None:
            _require_sha256("package_sha256", self.package_sha256)
        if self.package_manifest_sha256 is not None:
            _require_sha256("package_manifest_sha256", self.package_manifest_sha256)
        if self.failure is not None and not isinstance(self.failure, StructuredFailure):
            raise ValueError("failure must be StructuredFailure")
        object.__setattr__(self, "profile_name", profile_name)

    def status_dict(self) -> dict[str, object]:
        """Return a status-safe view suitable for a future API response."""
        return {
            "episode_id": str(self.episode_id),
            "state": self.state.value,
            "version": self.version,
            "source_sha256": self.source_sha256,
            "profile_name": self.profile_name,
            "package_manifest_sha256": self.package_manifest_sha256,
            "failure": self.failure.to_dict() if self.failure else None,
        }


class EpisodeRepository(Protocol):
    """Persistence port for immutable tenant-scoped episode records."""

    def create(self, record: EpisodeRecord) -> EpisodeRecord:
        """Create or replay one idempotent record."""

    def get(self, tenant_id: UUID, episode_id: UUID) -> EpisodeRecord:
        """Read one record within a tenant boundary."""

    def replace(
        self,
        tenant_id: UUID,
        record: EpisodeRecord,
        *,
        expected_version: int,
    ) -> EpisodeRecord:
        """Atomically replace one expected immutable version."""


class InMemoryEpisodeRepository:
    """Deterministic local repository adapter with tenant-safe idempotency."""

    def __init__(self) -> None:
        self._by_id: dict[UUID, EpisodeRecord] = {}
        self._create_by_key: dict[tuple[UUID, str], EpisodeRecord] = {}

    def create(self, record: EpisodeRecord) -> EpisodeRecord:
        key = (record.tenant_id, record.idempotency_key)
        existing = self._create_by_key.get(key)
        if existing is not None:
            if existing.request_fingerprint != record.request_fingerprint:
                raise IdempotencyConflict()
            return existing
        if record.episode_id in self._by_id:
            raise IdempotencyConflict()
        self._create_by_key[key] = record
        self._by_id[record.episode_id] = record
        return record

    def get(self, tenant_id: UUID, episode_id: UUID) -> EpisodeRecord:
        record = self._by_id.get(episode_id)
        if record is None or record.tenant_id != tenant_id:
            raise EpisodeNotFound()
        return record

    def replace(
        self,
        tenant_id: UUID,
        record: EpisodeRecord,
        *,
        expected_version: int,
    ) -> EpisodeRecord:
        current = self.get(tenant_id, record.episode_id)
        if record.tenant_id != tenant_id:
            raise EpisodeNotFound()
        if (
            record.tenant_id != current.tenant_id
            or record.project_id != current.project_id
            or record.episode_id != current.episode_id
            or record.idempotency_key != current.idempotency_key
            or record.profile_name != current.profile_name
            or record.source_sha256 != current.source_sha256
            or record.source_bytes != current.source_bytes
            or record.request_fingerprint != current.request_fingerprint
        ):
            raise ValueError("episode identity fields are immutable")
        if current.version != expected_version:
            raise VersionConflict()
        self._by_id[record.episode_id] = record
        return record


_TRANSITIONS: Mapping[EpisodeState, frozenset[EpisodeState]] = {
    EpisodeState.VALIDATED: frozenset({EpisodeState.SCRIPTED, EpisodeState.FAILED}),
    EpisodeState.SCRIPTED: frozenset({EpisodeState.RENDERED, EpisodeState.FAILED}),
    EpisodeState.RENDERED: frozenset({EpisodeState.QA_PASSED, EpisodeState.FAILED}),
    EpisodeState.QA_PASSED: frozenset({EpisodeState.PACKAGED, EpisodeState.FAILED}),
    EpisodeState.PACKAGED: frozenset({EpisodeState.PUBLISHED, EpisodeState.FAILED}),
    EpisodeState.PUBLISHED: frozenset(),
    EpisodeState.FAILED: frozenset(),
}


class EpisodeApplicationService:
    """Application boundary for source validation and lifecycle transitions."""

    def __init__(
        self,
        *,
        repository: EpisodeRepository,
        available_profiles: Collection[str],
        episode_id_factory: EpisodeIdFactory = _new_episode_id,
        clock: Clock = _now_utc,
    ) -> None:
        profiles = frozenset(available_profiles)
        if any(
            not isinstance(profile, str) or _PROFILE_ID.fullmatch(profile) is None
            for profile in profiles
        ):
            raise ValueError("available_profiles contains an invalid profile")
        self._repository = repository
        self._available_profiles = profiles
        self._episode_id_factory = episode_id_factory
        self._clock = clock

    def create_episode(self, command: EpisodeCreateCommand) -> EpisodeRecord:
        """Validate and snapshot one tenant-scoped episode creation request."""
        self._validate_command(command)
        try:
            source_text = command.source_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise EpisodeValidationError("source is not valid UTF-8") from error

        if command.profile_name not in self._available_profiles:
            raise EpisodeValidationError(f"Unknown profile: {command.profile_name}")
        validation = validate_markdown(
            source_text,
            self._available_profiles,
            default_profile=command.profile_name,
        )
        if not validation.accepted:
            raise EpisodeValidationError(_stable_validation_message(validation.errors))
        try:
            snapshot = snapshot_source(source_text)
        except (TypeError, ValueError) as error:
            raise EpisodeValidationError("source snapshot is invalid") from error
        source_sha256 = hashlib.sha256(command.source_bytes).hexdigest()
        if snapshot.source_sha256 != source_sha256:
            raise EpisodeValidationError("source snapshot hash is inconsistent")
        profile_name = command.profile_name
        raw_poddown = snapshot.frontmatter.get("poddown")
        if isinstance(raw_poddown, Mapping) and isinstance(
            raw_poddown.get("profile"), str
        ):
            profile_name = raw_poddown["profile"]
        fingerprint = _request_fingerprint(command)
        try:
            episode_id = self._episode_id_factory()
            _require_uuid7("episode_id", episode_id)
            now = self._clock()
            record = EpisodeRecord(
                tenant_id=command.tenant_id,
                project_id=command.project_id,
                episode_id=episode_id,
                idempotency_key=command.idempotency_key,
                profile_name=profile_name,
                source_sha256=source_sha256,
                source_bytes=len(command.source_bytes),
                request_fingerprint=fingerprint,
                state=EpisodeState.VALIDATED,
                version=1,
                created_at=now,
                updated_at=now,
            )
        except ValueError as error:
            raise EpisodeValidationError(str(error)) from error
        return self._repository.create(record)

    def get_episode(self, tenant_id: UUID, episode_id: UUID) -> EpisodeRecord:
        """Read one episode without crossing the tenant boundary."""
        try:
            _require_uuid7("tenant_id", tenant_id)
            _require_uuid7("episode_id", episode_id)
        except ValueError as error:
            raise EpisodeNotFound() from error
        return self._repository.get(tenant_id, episode_id)

    def transition(
        self,
        tenant_id: UUID,
        episode_id: UUID,
        target_state: EpisodeState,
        *,
        expected_version: int,
        qa_evidence: Mapping[str, object] | None = None,
        package_sha256: str | None = None,
        package_bytes: bytes | None = None,
        package_manifest_sha256: str | None = None,
        failure: StructuredFailure | None = None,
    ) -> EpisodeRecord:
        """Apply one explicit lifecycle transition with optimistic locking."""
        return self._transition(
            tenant_id,
            episode_id,
            target_state,
            expected_version=expected_version,
            qa_evidence=qa_evidence,
            package_sha256=package_sha256,
            package_bytes=package_bytes,
            package_manifest_sha256=package_manifest_sha256,
            failure=failure,
            publish_authorized=False,
        )

    def publish(
        self,
        tenant_id: UUID,
        episode_id: UUID,
        *,
        expected_version: int,
        authorized: bool,
    ) -> EpisodeRecord:
        """Publish only a packaged episode after an explicit authorization."""
        if authorized is not True:
            raise PublishAuthorizationError()
        return self._transition(
            tenant_id,
            episode_id,
            EpisodeState.PUBLISHED,
            expected_version=expected_version,
            qa_evidence=None,
            package_sha256=None,
            package_bytes=None,
            package_manifest_sha256=None,
            failure=None,
            publish_authorized=True,
        )

    def _transition(
        self,
        tenant_id: UUID,
        episode_id: UUID,
        target_state: EpisodeState,
        *,
        expected_version: int,
        qa_evidence: Mapping[str, object] | None,
        package_sha256: str | None,
        package_bytes: bytes | None,
        package_manifest_sha256: str | None,
        failure: StructuredFailure | None,
        publish_authorized: bool,
    ) -> EpisodeRecord:
        if not isinstance(target_state, EpisodeState):
            raise InvalidEpisodeTransition()
        if type(expected_version) is not int or expected_version < 1:
            raise VersionConflict()
        current = self.get_episode(tenant_id, episode_id)
        if current.version != expected_version:
            raise VersionConflict()
        if target_state not in _TRANSITIONS[current.state]:
            raise InvalidEpisodeTransition()
        if target_state is EpisodeState.PUBLISHED and not publish_authorized:
            raise InvalidEpisodeTransition()
        if target_state is EpisodeState.QA_PASSED:
            if not _qa_passed(qa_evidence):
                raise InvalidEpisodeTransition("passing QA evidence is required")
        elif qa_evidence is not None:
            raise InvalidEpisodeTransition()
        if target_state is EpisodeState.PACKAGED:
            if package_sha256 is None or _SHA256.fullmatch(package_sha256) is None:
                raise InvalidEpisodeTransition(
                    "a verified package checksum is required"
                )
            if current.state is not EpisodeState.QA_PASSED:
                raise InvalidEpisodeTransition("packaging requires passing QA")
            if (
                type(package_bytes) is not bytes
                or hashlib.sha256(package_bytes).hexdigest() != package_sha256
            ):
                raise InvalidEpisodeTransition("package bytes do not match checksum")
            if (
                package_manifest_sha256 is not None
                and _SHA256.fullmatch(package_manifest_sha256) is None
            ):
                raise InvalidEpisodeTransition(
                    "a verified package manifest checksum is required"
                )
        elif (
            package_sha256 is not None
            or package_bytes is not None
            or package_manifest_sha256 is not None
        ):
            raise InvalidEpisodeTransition()
        if target_state is EpisodeState.FAILED:
            if failure is None:
                raise InvalidEpisodeTransition(
                    "structured failure evidence is required"
                )
        elif failure is not None:
            raise InvalidEpisodeTransition()
        try:
            updated = replace(
                current,
                state=target_state,
                version=current.version + 1,
                updated_at=self._clock(),
                qa_evidence=qa_evidence
                if target_state is EpisodeState.QA_PASSED
                else current.qa_evidence,
                package_sha256=package_sha256
                if target_state is EpisodeState.PACKAGED
                else current.package_sha256,
                package_manifest_sha256=package_manifest_sha256
                if target_state is EpisodeState.PACKAGED
                else current.package_manifest_sha256,
                failure=failure if target_state is EpisodeState.FAILED else None,
            )
        except ValueError as error:
            raise InvalidEpisodeTransition(str(error)) from error
        return self._repository.replace(
            tenant_id,
            updated,
            expected_version=expected_version,
        )

    @staticmethod
    def _validate_command(command: EpisodeCreateCommand) -> None:
        if not isinstance(command, EpisodeCreateCommand):
            raise EpisodeValidationError("episode command is malformed")
        try:
            _require_uuid7("tenant_id", command.tenant_id)
            _require_uuid7("project_id", command.project_id)
            _require_text("idempotency key", command.idempotency_key, max_length=255)
            if type(command.source_bytes) is not bytes or not command.source_bytes:
                raise ValueError("source_bytes must be non-empty UTF-8 bytes")
            profile_name = _require_text(
                "profile name", command.profile_name, max_length=63
            )
            if _PROFILE_ID.fullmatch(profile_name) is None:
                raise ValueError("profile name has invalid format")
        except ValueError as error:
            raise EpisodeValidationError(str(error)) from error


def _request_fingerprint(command: EpisodeCreateCommand) -> str:
    material = (
        str(command.tenant_id).encode("ascii")
        + b"\x00"
        + str(command.project_id).encode("ascii")
        + b"\x00"
        + command.profile_name.encode("utf-8")
        + b"\x00"
        + command.source_bytes
    )
    return hashlib.sha256(material).hexdigest()


def _qa_passed(evidence: Mapping[str, object] | None) -> bool:
    if not isinstance(evidence, Mapping):
        return False
    return evidence.get("status") == "pass"


def _stable_validation_message(errors: tuple[str, ...]) -> str:
    """Map parser detail to a stable error without metadata leakage."""
    message = errors[0] if errors else "source validation failed"
    if message.startswith("Unknown profile:"):
        return message
    if message.startswith("Unknown PodDown key:"):
        return message
    if message.startswith("Invalid YAML frontmatter"):
        return "Invalid YAML frontmatter"
    if message.startswith("Markdown frontmatter"):
        return "Invalid Markdown frontmatter"
    return "Invalid PodDown metadata"


__all__ = [
    "EpisodeApplicationService",
    "EpisodeCreateCommand",
    "EpisodeNotFound",
    "EpisodeRecord",
    "EpisodeRepository",
    "EpisodeServiceError",
    "EpisodeState",
    "EpisodeValidationError",
    "IdempotencyConflict",
    "InMemoryEpisodeRepository",
    "InvalidEpisodeTransition",
    "PublishAuthorizationError",
    "StructuredFailure",
    "VersionConflict",
]
