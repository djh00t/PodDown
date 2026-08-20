"""Immutable Temporal orchestration for the complete episode pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

from poddown.audio.prepare_activity import PREPARE_CONTENT_ACTIVITY_NAME
from poddown.audio.workflow import (
    WORKFLOW_ACTIVITY_RETRY_POLICY,
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    EpisodeWorkflowResult,
    WorkflowContractError,
)
from poddown.audio.workflow import (
    workflow_id_for as render_result_workflow_id_for,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_MODES = frozenset({"deterministic-local", "host-local", "live-provider"})
_RENDER_PROVIDERS_BY_EXECUTION_MODE = {
    "deterministic-local": frozenset({"local"}),
    "host-local": frozenset({"host-local"}),
    "live-provider": frozenset({"elevenlabs"}),
}

VALIDATE_SOURCE_ACTIVITY_NAME = "poddown.audio.validate_source"
MASTER_EPISODE_ACTIVITY_NAME = "poddown.audio.master"
FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME = "poddown.audio.final_master_transcription"
MASTER_AND_FINAL_MASTER_QA_ACTIVITY_NAME = "poddown.audio.master_and_final_master_qa"
PACKAGE_EPISODE_ACTIVITY_NAME = "poddown.audio.package"
PUBLISH_EPISODE_ACTIVITY_NAME = "poddown.audio.publish"

PRODUCTION_INPUT_ERROR_TYPE = "ProductionWorkflowInputRejected"
PRODUCTION_STAGE_OUTPUT_ERROR_TYPE = "ProductionStageOutputRejected"
PRODUCTION_ACTIVITY_ERROR_TYPE = "ProductionStageActivityUnavailable"
PRODUCTION_RENDER_INPUT_ERROR_TYPE = "ProductionRenderInputRejected"
PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE = "ProductionPostRenderStagesUnavailable"
PRODUCTION_POST_RENDER_OUTPUT_ERROR_TYPE = "ProductionPostRenderOutputRejected"

_RENDER_WORKFLOW_INPUT_JSON_KEY = "episode_render_workflow_input_json"
_PREPARATION_ACTIVITY_INPUT_KEY = "preparation_activity_input"


class ProductionWorkflowInputError(ValueError):
    """Raised when an immutable production snapshot is unsafe."""


class ProductionStageOutputError(ValueError):
    """Raised when a stage returns incomplete or foreign evidence."""


class ProductionPostRenderStageError(ValueError):
    """Raised when post-render evidence cannot be accepted."""


class PackageCompletionError(ValueError):
    """Raised when an immutable package completion is unsafe."""


class PackageCompletionConflictError(PackageCompletionError):
    """Raised when a replay changes an immutable package completion."""


class ProductionStage(StrEnum):
    """Ordered production stages for one immutable episode snapshot."""

    VALIDATE_SOURCE = "validate_source"
    PREPARE_CONTENT = "prepare_content"
    RENDER_SEGMENTS = "render_segments"
    MASTER = "master"
    FINAL_MASTER_TRANSCRIPTION = "final_master_transcription"
    PACKAGE = "package"
    OPTIONAL_PUBLISH = "optional_publish"


def _required_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ProductionWorkflowInputError(f"{name} must be a non-empty string")


def _required_sha256(name: str, value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ProductionStageOutputError(f"{name} must be a SHA-256 hex digest")


def _freeze_json(value: object) -> object:
    """Copy JSON-native evidence into an immutable snapshot."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ProductionWorkflowInputError("snapshot contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProductionWorkflowInputError("snapshot keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    raise ProductionWorkflowInputError("snapshot contains a non-JSON value")


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _canonical_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    integer, decimal_point, fractional = format(value, "f").partition(".")
    fractional = fractional.rstrip("0")
    return integer if not fractional else f"{integer}{decimal_point}{fractional}"


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ProductionWorkflowInputError("production snapshot is malformed")
        value[key] = item
    return value


@dataclass(frozen=True, slots=True)
class ProductionWorkflowInput:
    """JSON-safe immutable identity and authority for one production run."""

    episode_id: str
    episode_version_id: str
    source_sha256: str
    profile_id: str
    prepared_content_reference: Mapping[str, object]
    execution_mode: Literal["deterministic-local", "host-local", "live-provider"]
    publish_target_id: str | None = None
    max_cost: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("episode_id", "episode_version_id", "profile_id"):
            _required_string(name, getattr(self, name))
        if (
            not isinstance(self.source_sha256, str)
            or _SHA256.fullmatch(self.source_sha256) is None
        ):
            raise ProductionWorkflowInputError(
                "source_sha256 must be a lowercase SHA-256 digest"
            )
        if self.execution_mode not in _EXECUTION_MODES:
            raise ProductionWorkflowInputError("execution_mode is not supported")
        if self.publish_target_id is not None:
            _required_string("publish_target_id", self.publish_target_id)
        if self.max_cost is not None and (
            not isinstance(self.max_cost, Decimal)
            or not self.max_cost.is_finite()
            or self.max_cost < 0
        ):
            raise ProductionWorkflowInputError(
                "max_cost must be a finite non-negative Decimal"
            )
        frozen = _freeze_json(self.prepared_content_reference)
        if not isinstance(frozen, Mapping) or not frozen:
            raise ProductionWorkflowInputError(
                "prepared_content_reference must be a non-empty mapping"
            )
        object.__setattr__(self, "prepared_content_reference", frozen)

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-native Temporal payload."""
        return {
            "episode_id": self.episode_id,
            "episode_version_id": self.episode_version_id,
            "source_sha256": self.source_sha256,
            "profile_id": self.profile_id,
            "prepared_content_reference": _json_value(self.prepared_content_reference),
            "execution_mode": self.execution_mode,
            "publish_target_id": self.publish_target_id,
            "max_cost": (
                _canonical_decimal(self.max_cost) if self.max_cost is not None else None
            ),
        }

    def to_json(self) -> str:
        """Serialize this snapshot canonically for Temporal."""
        return _canonical(self.to_dict())

    def digest(self) -> str:
        """Return the stable identity digest of this immutable snapshot."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, value: str) -> ProductionWorkflowInput:
        """Decode and validate one canonical production snapshot."""
        try:
            decoded = json.loads(value, object_pairs_hook=_unique_json_object)
        except ProductionWorkflowInputError:
            raise
        except (TypeError, json.JSONDecodeError) as error:
            raise ProductionWorkflowInputError(
                "production snapshot is malformed"
            ) from error
        if not isinstance(decoded, dict):
            raise ProductionWorkflowInputError("production snapshot is malformed")
        expected = {
            "episode_id",
            "episode_version_id",
            "source_sha256",
            "profile_id",
            "prepared_content_reference",
            "execution_mode",
            "publish_target_id",
            "max_cost",
        }
        if set(decoded) != expected:
            raise ProductionWorkflowInputError("production snapshot is malformed")
        raw_cost = decoded["max_cost"]
        max_cost: Decimal | None = None
        if raw_cost is not None:
            if not isinstance(raw_cost, str):
                raise ProductionWorkflowInputError("production snapshot is malformed")
            try:
                max_cost = Decimal(raw_cost)
            except (InvalidOperation, ValueError) as error:
                raise ProductionWorkflowInputError(
                    "production snapshot is malformed"
                ) from error
        try:
            return cls(
                episode_id=decoded["episode_id"],
                episode_version_id=decoded["episode_version_id"],
                source_sha256=decoded["source_sha256"],
                profile_id=decoded["profile_id"],
                prepared_content_reference=decoded["prepared_content_reference"],
                execution_mode=cast(Any, decoded["execution_mode"]),
                publish_target_id=decoded["publish_target_id"],
                max_cost=max_cost,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProductionWorkflowInputError(
                "production snapshot is malformed"
            ) from error


@dataclass(frozen=True, slots=True)
class MasterAndFinalMasterQaInput:
    """Immutable handoff from selected renders to the master activity."""

    episode_id: str
    episode_version_id: str
    render_input_sha256: str
    render_result_sha256: str
    selected_candidate_ids: tuple[str, ...]
    production_input_json: str
    preparation_result: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in (
            "episode_id",
            "episode_version_id",
            "render_input_sha256",
            "render_result_sha256",
            "production_input_json",
        ):
            _required_string(name, getattr(self, name))
        _required_sha256("render_input_sha256", self.render_input_sha256)
        _required_sha256("render_result_sha256", self.render_result_sha256)
        if (
            not isinstance(self.selected_candidate_ids, tuple)
            or not self.selected_candidate_ids
            or any(
                not isinstance(candidate_id, str) or not candidate_id
                for candidate_id in self.selected_candidate_ids
            )
            or len(set(self.selected_candidate_ids)) != len(self.selected_candidate_ids)
        ):
            raise ProductionStageOutputError(
                "selected_candidate_ids must be unique non-empty strings"
            )
        frozen = _freeze_json(self.preparation_result)
        if not isinstance(frozen, Mapping):
            raise ProductionStageOutputError("preparation result is malformed")
        object.__setattr__(self, "preparation_result", frozen)

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-native master activity payload."""
        return {
            "episode_id": self.episode_id,
            "episode_version_id": self.episode_version_id,
            "render_input_sha256": self.render_input_sha256,
            "render_result_sha256": self.render_result_sha256,
            "selected_candidate_ids": list(self.selected_candidate_ids),
            "production_input_json": self.production_input_json,
            "preparation_result": _json_value(self.preparation_result),
        }


def _package_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackageCompletionError(f"package object {name} is required")
    return value


@dataclass(frozen=True, slots=True)
class PackageObjectReference:
    """Immutable object metadata bound to one completed package."""

    name: str
    sha256: str
    storage_key: str
    byte_count: int
    media_type: str
    schema_version: str

    def __post_init__(self) -> None:
        _package_text("name", self.name)
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise PackageCompletionError("package object checksum is invalid")
        _package_text("storage key", self.storage_key)
        if type(self.byte_count) is not int or self.byte_count <= 0:
            raise PackageCompletionError("package object byte count is invalid")
        _package_text("media type", self.media_type)
        _package_text("schema version", self.schema_version)

    def to_dict(self) -> dict[str, object]:
        """Return the complete object reference for durable serialization."""
        return {
            "name": self.name,
            "sha256": self.sha256,
            "storage_key": self.storage_key,
            "byte_count": self.byte_count,
            "media_type": self.media_type,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PackageObjectReference:
        """Decode one persisted object reference."""
        try:
            name = _package_text("name", value["name"])
            sha256 = _package_text("sha256", value["sha256"])
            storage_key = _package_text("storage key", value["storage_key"])
            byte_count = value["byte_count"]
            media_type = _package_text("media type", value["media_type"])
            schema_version = _package_text("schema version", value["schema_version"])
            if type(byte_count) is not int:
                raise TypeError("byte_count must be an integer")
            return cls(
                name=name,
                sha256=sha256,
                storage_key=storage_key,
                byte_count=byte_count,
                media_type=media_type,
                schema_version=schema_version,
            )
        except (KeyError, TypeError) as error:
            raise PackageCompletionError(
                "package object metadata is malformed"
            ) from error


@dataclass(frozen=True, slots=True)
class MasterAndFinalMasterQaResult:
    """Final-master checksums and critical-token QA bound to one episode."""

    episode_id: str
    episode_version_id: str
    master_wav_checksum: str
    master_mp3_checksum: str
    qa_master_checksum: str
    qa_passed: bool
    qa_critical_token_accuracy: float

    def __post_init__(self) -> None:
        for name in ("episode_id", "episode_version_id"):
            _package_text(name, getattr(self, name))
        for name in (
            "master_wav_checksum",
            "master_mp3_checksum",
            "qa_master_checksum",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ProductionPostRenderStageError(f"{name} is invalid")
        if self.qa_master_checksum != self.master_wav_checksum:
            raise ProductionPostRenderStageError(
                "final-master QA checksum does not match master WAV"
            )
        if self.qa_passed is not True:
            raise ProductionPostRenderStageError("final-master QA did not pass")
        if (
            type(self.qa_critical_token_accuracy) is not float
            or self.qa_critical_token_accuracy != 1.0
        ):
            raise ProductionPostRenderStageError(
                "final-master QA critical-token accuracy must be exactly 1.0"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the complete final-master QA evidence."""
        return {
            "episode_id": self.episode_id,
            "episode_version_id": self.episode_version_id,
            "master_wav_checksum": self.master_wav_checksum,
            "master_mp3_checksum": self.master_mp3_checksum,
            "qa_master_checksum": self.qa_master_checksum,
            "qa_passed": self.qa_passed,
            "qa_critical_token_accuracy": self.qa_critical_token_accuracy,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> MasterAndFinalMasterQaResult:
        """Decode persisted final-master QA evidence."""
        try:
            episode_id = _package_text("episode_id", value["episode_id"])
            episode_version_id = _package_text(
                "episode_version_id", value["episode_version_id"]
            )
            master_wav_checksum = _package_text(
                "master_wav_checksum", value["master_wav_checksum"]
            )
            master_mp3_checksum = _package_text(
                "master_mp3_checksum", value["master_mp3_checksum"]
            )
            qa_master_checksum = _package_text(
                "qa_master_checksum", value["qa_master_checksum"]
            )
            qa_passed = value["qa_passed"]
            qa_critical_token_accuracy = value["qa_critical_token_accuracy"]
            if type(qa_passed) is not bool:
                raise TypeError("qa_passed must be a boolean")
            if type(qa_critical_token_accuracy) is not float:
                raise TypeError("qa_critical_token_accuracy must be a float")
            return cls(
                episode_id=episode_id,
                episode_version_id=episode_version_id,
                master_wav_checksum=master_wav_checksum,
                master_mp3_checksum=master_mp3_checksum,
                qa_master_checksum=qa_master_checksum,
                qa_passed=qa_passed,
                qa_critical_token_accuracy=qa_critical_token_accuracy,
            )
        except (KeyError, TypeError) as error:
            raise PackageCompletionError(
                "final-master QA evidence is malformed"
            ) from error


@dataclass(frozen=True, slots=True)
class PackageCompletion:
    """One immutable, checksum-bound terminal package record."""

    episode_id: str
    episode_version_id: str
    final_master_qa: MasterAndFinalMasterQaResult
    wav_object: PackageObjectReference
    mp3_object: PackageObjectReference
    manifest_object: PackageObjectReference
    status: Literal["completed"] = "completed"

    def __post_init__(self) -> None:
        _package_text("episode_id", self.episode_id)
        _package_text("episode_version_id", self.episode_version_id)
        if not isinstance(self.final_master_qa, MasterAndFinalMasterQaResult):
            raise PackageCompletionError("final-master QA evidence is invalid")
        if (
            self.final_master_qa.episode_id != self.episode_id
            or self.final_master_qa.episode_version_id != self.episode_version_id
        ):
            raise PackageCompletionError(
                "final-master QA evidence does not match package episode version"
            )
        for reference in (self.wav_object, self.mp3_object, self.manifest_object):
            if not isinstance(reference, PackageObjectReference):
                raise PackageCompletionError("package object reference is invalid")
        if self.wav_object.name != "episode.wav":
            raise PackageCompletionError("package WAV object reference is invalid")
        if self.mp3_object.name != "episode.mp3":
            raise PackageCompletionError("package MP3 object reference is invalid")
        if self.manifest_object.name != "package-manifest.json":
            raise PackageCompletionError("package manifest object reference is invalid")
        if self.wav_object.sha256 != self.final_master_qa.master_wav_checksum:
            raise PackageCompletionError(
                "package WAV checksum does not match final-master QA"
            )
        if self.mp3_object.sha256 != self.final_master_qa.master_mp3_checksum:
            raise PackageCompletionError(
                "package MP3 checksum does not match final-master QA"
            )
        if self.status != "completed":
            raise PackageCompletionError("package completion status must be completed")

    def to_dict(self) -> dict[str, object]:
        """Return the complete package completion record."""
        return {
            "episode_id": self.episode_id,
            "episode_version_id": self.episode_version_id,
            "final_master_qa": self.final_master_qa.to_dict(),
            "wav_object": self.wav_object.to_dict(),
            "mp3_object": self.mp3_object.to_dict(),
            "manifest_object": self.manifest_object.to_dict(),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PackageCompletion:
        """Decode one persisted package completion record."""
        try:
            episode_id = _package_text("episode_id", value["episode_id"])
            episode_version_id = _package_text(
                "episode_version_id", value["episode_version_id"]
            )
            qa = value["final_master_qa"]
            wav = value["wav_object"]
            mp3 = value["mp3_object"]
            manifest = value["manifest_object"]
            status = value["status"]
            if not all(isinstance(item, Mapping) for item in (qa, wav, mp3, manifest)):
                raise TypeError
            if status != "completed":
                raise TypeError("package completion status is invalid")
            return cls(
                episode_id=episode_id,
                episode_version_id=episode_version_id,
                final_master_qa=MasterAndFinalMasterQaResult.from_dict(
                    cast(Mapping[str, object], qa)
                ),
                wav_object=PackageObjectReference.from_dict(
                    cast(Mapping[str, object], wav)
                ),
                mp3_object=PackageObjectReference.from_dict(
                    cast(Mapping[str, object], mp3)
                ),
                manifest_object=PackageObjectReference.from_dict(
                    cast(Mapping[str, object], manifest)
                ),
                status=status,
            )
        except (KeyError, TypeError) as error:
            raise PackageCompletionError("package completion is malformed") from error


def package_completion_for(
    final_master_qa: MasterAndFinalMasterQaResult,
    *,
    wav_object: PackageObjectReference | None,
    mp3_object: PackageObjectReference | None,
    manifest_object: PackageObjectReference | None,
) -> PackageCompletion:
    """Build a completion only when every final-master identity is exact."""
    if not isinstance(final_master_qa, MasterAndFinalMasterQaResult):
        raise PackageCompletionError("final-master QA evidence is required")
    if not isinstance(wav_object, PackageObjectReference):
        raise PackageCompletionError("WAV object reference is required")
    if not isinstance(mp3_object, PackageObjectReference):
        raise PackageCompletionError("MP3 object reference is required")
    if not isinstance(manifest_object, PackageObjectReference):
        raise PackageCompletionError("manifest object reference is required")
    return PackageCompletion(
        episode_id=final_master_qa.episode_id,
        episode_version_id=final_master_qa.episode_version_id,
        final_master_qa=final_master_qa,
        wav_object=wav_object,
        mp3_object=mp3_object,
        manifest_object=manifest_object,
    )


class PackageCompletionRepository(Protocol):
    """Atomic persistence port for package refs and completed status."""

    def commit_package_completion(
        self, completion: PackageCompletion
    ) -> PackageCompletion:
        """Commit or replay all package completion evidence in one operation."""


class InMemoryPackageCompletionRepository:
    """Deterministic local repository for atomic package-completion tests."""

    def __init__(self, *, fail_next_commit: bool = False) -> None:
        self._records: dict[tuple[str, str], PackageCompletion] = {}
        self._fail_next_commit = fail_next_commit

    def commit_package_completion(
        self, completion: PackageCompletion
    ) -> PackageCompletion:
        """Atomically insert or replay one immutable package completion."""
        if not isinstance(completion, PackageCompletion):
            raise PackageCompletionError("package completion is required")
        key = (completion.episode_id, completion.episode_version_id)
        current = self._records.get(key)
        if current is not None:
            if current != completion:
                raise PackageCompletionConflictError(
                    "package completion identity conflicts with the existing record"
                )
            return current
        if self._fail_next_commit:
            self._fail_next_commit = False
            raise PackageCompletionError("atomic package commit failed")
        self._records[key] = completion
        return completion

    def get(self, episode_id: str, episode_version_id: str) -> PackageCompletion | None:
        """Return one immutable completion or no record."""
        return self._records.get((episode_id, episode_version_id))


@dataclass(frozen=True, slots=True)
class ProductionWorkflowResult:
    """Safe terminal evidence returned by the complete workflow."""

    workflow_id: str
    status: Literal["completed"]
    stage: Literal["published", "packaged"]
    package_sha256: str
    package_manifest_sha256: str
    publication: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _required_string("workflow_id", self.workflow_id)
        _required_sha256("package_sha256", self.package_sha256)
        _required_sha256("package_manifest_sha256", self.package_manifest_sha256)
        if self.status != "completed" or self.stage not in {"packaged", "published"}:
            raise ProductionStageOutputError("production workflow result is incomplete")
        if self.publication is not None:
            frozen = _freeze_json(self.publication)
            if not isinstance(frozen, Mapping):
                raise ProductionStageOutputError("publication result is malformed")
            object.__setattr__(self, "publication", frozen)

    def to_json(self) -> str:
        return _canonical(
            {
                "workflow_id": self.workflow_id,
                "status": self.status,
                "stage": self.stage,
                "package_sha256": self.package_sha256,
                "package_manifest_sha256": self.package_manifest_sha256,
                "publication": _json_value(self.publication),
            }
        )


def stage_plan_for(value: ProductionWorkflowInput) -> tuple[ProductionStage, ...]:
    """Return the deterministic stage sequence for one production snapshot."""
    if not isinstance(value, ProductionWorkflowInput):
        raise TypeError("workflow_input must be ProductionWorkflowInput")
    stages = (
        ProductionStage.VALIDATE_SOURCE,
        ProductionStage.PREPARE_CONTENT,
        ProductionStage.RENDER_SEGMENTS,
        ProductionStage.MASTER,
        ProductionStage.FINAL_MASTER_TRANSCRIPTION,
        ProductionStage.PACKAGE,
    )
    return (
        (*stages, ProductionStage.OPTIONAL_PUBLISH)
        if value.publish_target_id
        else stages
    )


def workflow_id_for(value: ProductionWorkflowInput) -> str:
    """Return the stable Temporal workflow ID for one snapshot."""
    if not isinstance(value, ProductionWorkflowInput):
        raise TypeError("workflow_input must be ProductionWorkflowInput")
    return f"episode-production-{value.digest()}"


def render_workflow_input_for(value: ProductionWorkflowInput) -> EpisodeWorkflowInput:
    """Validate the render child snapshot against production authority."""
    if not isinstance(value, ProductionWorkflowInput):
        raise TypeError("workflow_input must be ProductionWorkflowInput")
    payload = value.prepared_content_reference.get(_RENDER_WORKFLOW_INPUT_JSON_KEY)
    if not isinstance(payload, str):
        raise ProductionWorkflowInputError("production render input is invalid")
    return _render_workflow_input_from_json(value, payload)


def _render_workflow_input_from_json(
    value: ProductionWorkflowInput, payload: str
) -> EpisodeWorkflowInput:
    """Decode and validate a render snapshot against production authority."""
    try:
        decoded = json.loads(payload, object_pairs_hook=_unique_json_object)
        if not isinstance(decoded, dict):
            raise ProductionWorkflowInputError("production render input is invalid")
        render_input = EpisodeWorkflowInput.from_dict(cast(dict[str, Any], decoded))
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
        WorkflowContractError,
    ) as error:
        raise ProductionWorkflowInputError(
            "production render input is invalid"
        ) from error
    allowed = _RENDER_PROVIDERS_BY_EXECUTION_MODE[value.execution_mode]
    if render_input.episode_id != value.episode_id or any(
        segment.render_request.provider not in allowed
        or segment.render_request.episode_id != value.episode_id
        for segment in render_input.segments
    ):
        raise ProductionWorkflowInputError("production render input is invalid")
    return render_input


def render_workflow_input_from_preparation(
    value: ProductionWorkflowInput, preparation: Mapping[str, object]
) -> EpisodeWorkflowInput:
    """Resolve a render snapshot projected by the preparation activity."""
    if not isinstance(value, ProductionWorkflowInput):
        raise TypeError("workflow_input must be ProductionWorkflowInput")
    if not isinstance(preparation, Mapping):
        raise ProductionWorkflowInputError("preparation evidence is invalid")
    payload = preparation.get("render_workflow_input_json")
    if not isinstance(payload, str):
        raise ProductionWorkflowInputError(
            "preparation render input projection is missing"
        )
    render_input = _render_workflow_input_from_json(value, payload)
    if render_input.episode_version != value.episode_version_id:
        raise ProductionWorkflowInputError(
            "prepared render input version does not match production authority"
        )
    raw_tokens = preparation.get("critical_tokens")
    if not isinstance(raw_tokens, list):
        raise ProductionWorkflowInputError(
            "preparation critical-token projection is missing"
        )
    expected_tokens: list[str] = []
    for item in raw_tokens:
        if not isinstance(item, Mapping) or not isinstance(
            item.get("expected_spoken_form"), str
        ):
            raise ProductionWorkflowInputError(
                "preparation critical-token projection is invalid"
            )
        expected_tokens.append(item["expected_spoken_form"])
    actual_tokens = [
        token for segment in render_input.segments for token in segment.critical_tokens
    ]
    if tuple(actual_tokens) != tuple(expected_tokens):
        raise ProductionWorkflowInputError(
            "prepared render input critical-token identity does not match"
        )
    return render_input


def render_workflow_id_for(value: ProductionWorkflowInput) -> str:
    """Return the replay-safe child ID for the render stage."""
    return f"{workflow_id_for(value)}-render"


def preparation_activity_input_for(
    value: ProductionWorkflowInput,
) -> Mapping[str, object]:
    """Return the required immutable preparation activity payload."""
    raw = value.prepared_content_reference.get(_PREPARATION_ACTIVITY_INPUT_KEY)
    if not isinstance(raw, Mapping):
        raise ProductionWorkflowInputError("production preparation input is invalid")
    return cast(Mapping[str, object], _json_value(raw))


def master_and_final_master_qa_input_for(
    value: ProductionWorkflowInput,
    render_input: EpisodeWorkflowInput,
    render_result_json: object,
    preparation_result: Mapping[str, object],
) -> MasterAndFinalMasterQaInput:
    """Bind selected render candidates to mastering and QA."""
    if not isinstance(render_result_json, str):
        raise ProductionStageOutputError("render workflow result is malformed")
    try:
        render_result = EpisodeWorkflowResult.from_json(render_result_json)
    except (TypeError, ValueError, WorkflowContractError) as error:
        raise ProductionStageOutputError(
            "render workflow result is malformed"
        ) from error
    if (
        render_result.status != "completed"
        or render_result.workflow_id != render_result_workflow_id_for(render_input)
        or len(render_result.decisions) != len(render_input.segments)
        or tuple(decision.segment_id for decision in render_result.decisions)
        != tuple(segment.segment_id for segment in render_input.segments)
        or any(
            decision.accepted_candidate_id is None
            or decision.accepted_candidate_id
            not in {candidate.candidate_id for candidate in decision.candidates}
            for decision in render_result.decisions
        )
    ):
        raise ProductionStageOutputError(
            "render workflow result has no selected candidates"
        )
    return MasterAndFinalMasterQaInput(
        episode_id=value.episode_id,
        episode_version_id=value.episode_version_id,
        render_input_sha256=hashlib.sha256(
            render_input.to_json().encode("utf-8")
        ).hexdigest(),
        render_result_sha256=hashlib.sha256(
            render_result.to_json().encode("utf-8")
        ).hexdigest(),
        selected_candidate_ids=tuple(
            cast(str, decision.accepted_candidate_id)
            for decision in render_result.decisions
        ),
        production_input_json=value.to_json(),
        preparation_result=_preparation_reference(
            preparation_result,
            include_render_context=_RENDER_WORKFLOW_INPUT_JSON_KEY
            not in value.prepared_content_reference,
        ),
    )


def _activity_id(value: ProductionWorkflowInput, stage: str) -> str:
    return f"{workflow_id_for(value)}-{stage}"


def _stage_mapping(value: object, stage: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProductionStageOutputError(f"{stage} activity output is malformed")
    return cast(Mapping[str, object], value)


def _require_stage_identity(
    output: Mapping[str, object], value: ProductionWorkflowInput, stage: str
) -> None:
    if (
        output.get("episode_id") != value.episode_id
        or output.get("episode_version_id") != value.episode_version_id
    ):
        raise ProductionStageOutputError(
            f"{stage} evidence is bound to another episode"
        )


def _require_passed(output: Mapping[str, object], stage: str) -> None:
    if output.get("passed") is not True:
        raise ProductionStageOutputError(f"{stage} did not pass")


def _preparation_reference(
    preparation_result: Mapping[str, object],
    *,
    include_render_context: bool = False,
) -> Mapping[str, object]:
    """Keep only preparation identity needed by downstream stage records."""
    reference: dict[str, object] = {}
    for name in (
        "source_sha256",
        "resolved_profile_id",
        "resolved_profile_version",
        "manifest_sha256",
    ):
        value = preparation_result.get(name)
        if not isinstance(value, str) or not value:
            raise ProductionStageOutputError(f"preparation evidence is missing {name}")
        reference[name] = value
    _required_sha256("preparation source_sha256", reference["source_sha256"])
    _required_sha256("preparation manifest_sha256", reference["manifest_sha256"])
    if not include_render_context:
        return reference
    render_input = preparation_result.get("render_workflow_input_json")
    if render_input is not None:
        if not isinstance(render_input, str) or not render_input:
            raise ProductionStageOutputError(
                "preparation render input projection is invalid"
            )
        reference["render_workflow_input_json"] = render_input
    raw_tokens = preparation_result.get("critical_tokens")
    if raw_tokens is not None:
        if not isinstance(raw_tokens, list):
            raise ProductionStageOutputError(
                "preparation critical-token projection is invalid"
            )
        reference["critical_tokens"] = list(raw_tokens)
    return reference


def build_validate_source_activity() -> Any:
    """Build the source identity gate used by the production worker."""

    @activity.defn(name=VALIDATE_SOURCE_ACTIVITY_NAME)
    def validate(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source_sha256 = payload["source_sha256"]
            episode_id = payload["episode_id"]
            episode_version_id = payload["episode_version_id"]
            profile_id = payload["profile_id"]
            reference = payload["prepared_content_reference"]
            if not all(
                isinstance(value, str) and value
                for value in (
                    source_sha256,
                    episode_id,
                    episode_version_id,
                    profile_id,
                )
            ) or not isinstance(reference, Mapping):
                raise ValueError("source validation payload is malformed")
            preparation = reference.get(_PREPARATION_ACTIVITY_INPUT_KEY)
            if not isinstance(preparation, Mapping):
                raise ValueError("source validation preparation input is missing")
            source = preparation.get("source_markdown")
            if (
                not isinstance(source, str)
                or hashlib.sha256(source.encode("utf-8")).hexdigest() != source_sha256
            ):
                raise ValueError("source validation hash mismatch")
            if preparation.get("profile_id") != profile_id:
                raise ValueError("source validation profile mismatch")
            return {
                "episode_id": episode_id,
                "episode_version_id": episode_version_id,
                "passed": True,
                "source_sha256": source_sha256,
            }
        except (KeyError, TypeError, ValueError):
            raise ApplicationError(
                "source validation rejected",
                type=PRODUCTION_STAGE_OUTPUT_ERROR_TYPE,
                non_retryable=True,
            ) from None

    return validate


def build_unavailable_production_activity(name: str) -> Any:
    """Build an explicit fail-closed adapter for an unconfigured stage."""
    if not isinstance(name, str) or not name:
        raise ValueError("activity name must be non-empty")

    @activity.defn(name=name)
    async def unavailable(payload: Any) -> None:
        del payload
        raise ApplicationError(
            "production stage activity is not configured",
            type=PRODUCTION_ACTIVITY_ERROR_TYPE,
            non_retryable=True,
        )

    return unavailable


@workflow.defn(name="EpisodeProductionWorkflow")
class EpisodeProductionWorkflow:
    """Run all durable production stages from one immutable snapshot."""

    async def _run_legacy_render_contract(
        self, production_input: ProductionWorkflowInput
    ) -> str:
        """Preserve the earlier render-to-master contract for stored snapshots.

        Historical P3 snapshots may contain an already prepared render input
        but no preparation-activity input.  They are still useful replay
        fixtures, so keep their explicit fail-closed render/master boundary
        without weakening the current durable stage path.
        """
        try:
            render_input = render_workflow_input_for(production_input)
        except ProductionWorkflowInputError as error:
            raise ApplicationError(
                "production render input is invalid",
                type=PRODUCTION_RENDER_INPUT_ERROR_TYPE,
                non_retryable=True,
            ) from error

        render_result_json = await workflow.execute_child_workflow(
            EpisodeRenderWorkflow.run,
            render_input.to_json(),
            id=render_workflow_id_for(production_input),
        )
        try:
            if not isinstance(render_result_json, str):
                raise ProductionPostRenderStageError(
                    "render workflow result is malformed"
                )
            json.loads(render_result_json, object_pairs_hook=_unique_json_object)
            render_result = EpisodeWorkflowResult.from_json(render_result_json)
            if (
                render_result.status != "completed"
                or render_result.workflow_id
                != render_result_workflow_id_for(render_input)
                or len(render_result.decisions) != len(render_input.segments)
                or tuple(decision.segment_id for decision in render_result.decisions)
                != tuple(segment.segment_id for segment in render_input.segments)
                or any(
                    decision.accepted_candidate_id is None
                    or decision.accepted_candidate_id
                    not in {candidate.candidate_id for candidate in decision.candidates}
                    for decision in render_result.decisions
                )
            ):
                raise ProductionPostRenderStageError(
                    "render workflow result has no selected candidates"
                )
            handoff = {
                "episode_id": production_input.episode_id,
                "episode_version_id": production_input.episode_version_id,
                "render_input_json": render_input.to_json(),
                "render_result_json": render_result.to_json(),
                "selected_candidate_ids": [
                    decision.accepted_candidate_id
                    for decision in render_result.decisions
                ],
            }
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ProductionPostRenderStageError,
            WorkflowContractError,
        ) as error:
            raise ApplicationError(
                "production stages after render are not registered",
                type=PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE,
                non_retryable=True,
            ) from error

        try:
            raw_result = await workflow.execute_activity(
                MASTER_AND_FINAL_MASTER_QA_ACTIVITY_NAME,
                args=[handoff],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=WORKFLOW_ACTIVITY_RETRY_POLICY,
                activity_id=_activity_id(production_input, "master-final-master-qa"),
            )
            if not isinstance(raw_result, Mapping):
                raise ProductionPostRenderStageError(
                    "mastering and final-master QA evidence is malformed"
                )
            result = MasterAndFinalMasterQaResult.from_dict(raw_result)
            if (
                result.episode_id != production_input.episode_id
                or result.episode_version_id != production_input.episode_version_id
            ):
                raise ProductionPostRenderStageError(
                    "mastering and final-master QA evidence does not match "
                    "episode snapshot"
                )
        except (TypeError, ValueError, PackageCompletionError) as error:
            raise ApplicationError(
                "mastering and final-master QA evidence is invalid",
                type=PRODUCTION_POST_RENDER_OUTPUT_ERROR_TYPE,
                non_retryable=True,
            ) from error

        raise ApplicationError(
            "production stages after render are not registered",
            type=PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE,
            non_retryable=True,
        )

    @workflow.run
    async def run(self, production_input_json: str) -> str:
        try:
            production_input = ProductionWorkflowInput.from_json(production_input_json)
            reference = production_input.prepared_content_reference
            if _PREPARATION_ACTIVITY_INPUT_KEY not in reference:
                return await self._run_legacy_render_contract(production_input)
            has_render_input = isinstance(
                reference.get(_RENDER_WORKFLOW_INPUT_JSON_KEY),
                str,
            )
            render_input = (
                render_workflow_input_for(production_input)
                if has_render_input
                else None
            )
            preparation_input = preparation_activity_input_for(production_input)
        except ProductionWorkflowInputError as error:
            raise ApplicationError(
                "production workflow input is invalid",
                type=PRODUCTION_INPUT_ERROR_TYPE,
                non_retryable=True,
            ) from error

        try:
            validation_raw = await workflow.execute_activity(
                VALIDATE_SOURCE_ACTIVITY_NAME,
                args=[production_input.to_dict()],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=WORKFLOW_ACTIVITY_RETRY_POLICY,
                activity_id=_activity_id(production_input, "validate-source"),
            )
            validation = _stage_mapping(validation_raw, "validate_source")
            _require_stage_identity(validation, production_input, "validate_source")
            _require_passed(validation, "validate_source")

            prepared_raw = await workflow.execute_activity(
                PREPARE_CONTENT_ACTIVITY_NAME,
                args=[dict(preparation_input)],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=WORKFLOW_ACTIVITY_RETRY_POLICY,
                activity_id=_activity_id(production_input, "prepare-content"),
            )
            prepared = _stage_mapping(prepared_raw, "prepare_content")
            if prepared.get("source_sha256") != production_input.source_sha256:
                raise ProductionStageOutputError(
                    "prepare_content evidence is bound to another source"
                )
            if prepared.get("resolved_profile_id") != production_input.profile_id:
                raise ProductionStageOutputError(
                    "prepare_content evidence is bound to another profile"
                )
            if render_input is None:
                try:
                    render_input = render_workflow_input_from_preparation(
                        production_input, prepared
                    )
                except ProductionWorkflowInputError as error:
                    raise ProductionStageOutputError(str(error)) from error

            render_result_json = await workflow.execute_child_workflow(
                EpisodeRenderWorkflow.run,
                render_input.to_json(),
                id=render_workflow_id_for(production_input),
            )
            handoff = master_and_final_master_qa_input_for(
                production_input,
                render_input,
                render_result_json,
                prepared,
            )
            master_raw = await workflow.execute_activity(
                MASTER_EPISODE_ACTIVITY_NAME,
                args=[handoff.to_dict()],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=WORKFLOW_ACTIVITY_RETRY_POLICY,
                activity_id=_activity_id(production_input, "master"),
            )
            master = _stage_mapping(master_raw, "master")
            _require_stage_identity(master, production_input, "master")
            _require_passed(master, "master")
            _required_sha256("master_wav_checksum", master.get("master_wav_checksum"))
            _required_sha256("master_mp3_checksum", master.get("master_mp3_checksum"))

            qa_raw = await workflow.execute_activity(
                FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME,
                args=[
                    {
                        "production_input": production_input.to_dict(),
                        "master": dict(master),
                    }
                ],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=WORKFLOW_ACTIVITY_RETRY_POLICY,
                activity_id=_activity_id(
                    production_input, "final-master-transcription"
                ),
            )
            qa = _stage_mapping(qa_raw, "final_master_transcription")
            _require_stage_identity(qa, production_input, "final_master_transcription")
            _require_passed(qa, "final_master_transcription")
            if qa.get("qa_master_checksum") != master.get("master_wav_checksum"):
                raise ProductionStageOutputError(
                    "final-master QA checksum is not bound to the master"
                )
            if qa.get("qa_critical_token_accuracy") != 1.0:
                raise ProductionStageOutputError(
                    "final-master QA critical-token accuracy must be exactly 1.0"
                )

            package_raw = await workflow.execute_activity(
                PACKAGE_EPISODE_ACTIVITY_NAME,
                args=[
                    {
                        "production_input": production_input.to_dict(),
                        "preparation": dict(handoff.preparation_result),
                        "handoff": handoff.to_dict(),
                        "master": dict(master),
                        "qa": dict(qa),
                    }
                ],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=WORKFLOW_ACTIVITY_RETRY_POLICY,
                activity_id=_activity_id(production_input, "package"),
            )
            package = _stage_mapping(package_raw, "package")
            _require_stage_identity(package, production_input, "package")
            _require_passed(package, "package")
            package_sha256 = package.get("package_sha256")
            manifest_sha256 = package.get("package_manifest_sha256")
            _required_sha256("package_sha256", package_sha256)
            _required_sha256("package_manifest_sha256", manifest_sha256)

            publication: Mapping[str, object] | None = None
            stage: Literal["packaged", "published"] = "packaged"
            if production_input.publish_target_id is not None:
                publish_payload = production_input.prepared_content_reference.get(
                    "publish_payload"
                )
                if not isinstance(publish_payload, Mapping):
                    raise ProductionStageOutputError(
                        "optional publication authority is unavailable"
                    )
                publication_raw = await workflow.execute_activity(
                    PUBLISH_EPISODE_ACTIVITY_NAME,
                    args=[
                        {
                            **dict(publish_payload),
                            "package_sha256": package_sha256,
                            "package_manifest_sha256": manifest_sha256,
                        }
                    ],
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=WORKFLOW_ACTIVITY_RETRY_POLICY,
                    activity_id=_activity_id(production_input, "publish"),
                )
                publication = _stage_mapping(publication_raw, "optional_publish")
                _require_stage_identity(
                    publication, production_input, "optional_publish"
                )
                _require_passed(publication, "optional_publish")
                stage = "published"

            return ProductionWorkflowResult(
                workflow_id=workflow_id_for(production_input),
                status="completed",
                stage=stage,
                package_sha256=cast(str, package_sha256),
                package_manifest_sha256=cast(str, manifest_sha256),
                publication=publication,
            ).to_json()
        except ProductionStageOutputError as error:
            raise ApplicationError(
                "production stage output is invalid",
                type=PRODUCTION_STAGE_OUTPUT_ERROR_TYPE,
                non_retryable=True,
            ) from error


__all__ = [
    "FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME",
    "MASTER_AND_FINAL_MASTER_QA_ACTIVITY_NAME",
    "MASTER_EPISODE_ACTIVITY_NAME",
    "PACKAGE_EPISODE_ACTIVITY_NAME",
    "PUBLISH_EPISODE_ACTIVITY_NAME",
    "PREPARE_CONTENT_ACTIVITY_NAME",
    "PRODUCTION_ACTIVITY_ERROR_TYPE",
    "PRODUCTION_INPUT_ERROR_TYPE",
    "PRODUCTION_POST_RENDER_OUTPUT_ERROR_TYPE",
    "PRODUCTION_POST_RENDER_STAGE_ERROR_TYPE",
    "PRODUCTION_RENDER_INPUT_ERROR_TYPE",
    "PRODUCTION_STAGE_OUTPUT_ERROR_TYPE",
    "VALIDATE_SOURCE_ACTIVITY_NAME",
    "EpisodeProductionWorkflow",
    "InMemoryPackageCompletionRepository",
    "MasterAndFinalMasterQaInput",
    "MasterAndFinalMasterQaResult",
    "PackageCompletion",
    "PackageCompletionConflictError",
    "PackageCompletionError",
    "PackageCompletionRepository",
    "PackageObjectReference",
    "ProductionStage",
    "ProductionPostRenderStageError",
    "ProductionStageOutputError",
    "ProductionWorkflowInput",
    "ProductionWorkflowInputError",
    "ProductionWorkflowResult",
    "master_and_final_master_qa_input_for",
    "build_unavailable_production_activity",
    "build_validate_source_activity",
    "preparation_activity_input_for",
    "package_completion_for",
    "render_workflow_id_for",
    "render_workflow_input_for",
    "render_workflow_input_from_preparation",
    "stage_plan_for",
    "workflow_id_for",
]
