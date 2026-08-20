"""Temporal boundary for deterministic, source-bound content preparation."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from temporalio import activity
from temporalio.exceptions import ApplicationError

from poddown.content.adaptation import AdaptationError
from poddown.content.segmentation import SegmentationError
from poddown.content.service import (
    ContentPreparationRequest,
    ContentPreparationResult,
    LiveAdaptationPort,
    prepare_content,
    prepare_content_live,
)

PREPARE_CONTENT_ACTIVITY_NAME = "prepare_content"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREPARATION_REJECTED_MESSAGE = "content preparation rejected"
_PREPARATION_REJECTED_TYPE = "ContentPreparationRejectedError"

PreparationRequestFactory = Callable[
    ["PrepareContentActivityInput"], ContentPreparationRequest
]
RenderSnapshotFactory = Callable[
    ["PrepareContentActivityInput", ContentPreparationResult], str
]
LiveAdaptationFactory = Callable[["PrepareContentActivityInput"], LiveAdaptationPort]
PreparedContentRecorder = Callable[
    ["PrepareContentActivityInput", ContentPreparationResult], None
]


def _required_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _required_sha256(name: str, value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _freeze_json_value(value: object) -> object:
    """Recursively freeze a JSON-safe evidence value for workflow handoff."""
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("preparation evidence keys must be strings")
            frozen[key] = _freeze_json_value(item)
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(_freeze_json_value(item) for item in value)
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise ValueError("preparation evidence must be JSON-safe")


def _frozen_reference(value: Mapping[str, object]) -> Mapping[str, object]:
    """Copy a service reference into a stable immutable transport value."""
    frozen = _freeze_json_value(value)
    if not isinstance(frozen, Mapping):
        raise ValueError("preparation reference must be a mapping")
    return frozen


def _json_native_copy(value: object) -> object:
    """Recursively copy frozen evidence into independent JSON-native values."""
    if isinstance(value, Mapping):
        return {key: _json_native_copy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_native_copy(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PrepareContentActivityInput:
    """Identity-bound source snapshot crossing the preparation activity."""

    tenant_id: str
    project_id: str
    episode_id: str
    source_markdown: str
    profile_id: str
    prepared_content_key: str | None = None
    episode_version_id: str | None = None
    execution_mode: (
        Literal["deterministic-local", "host-local", "live-provider"] | None
    ) = None
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "episode_id", "profile_id"):
            _required_string(name, getattr(self, name))
        if not isinstance(self.source_markdown, str):
            raise ValueError("source_markdown must be a string")
        if self.prepared_content_key is not None:
            _required_sha256("prepared_content_key", self.prepared_content_key)
        if self.episode_version_id is not None:
            _required_string("episode_version_id", self.episode_version_id)
        if self.execution_mode is not None and self.execution_mode not in {
            "deterministic-local",
            "host-local",
            "live-provider",
        }:
            raise ValueError("execution_mode is invalid")
        if self.max_attempts is not None and (
            type(self.max_attempts) is not int or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be positive")

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-native Temporal payload shape."""
        value: dict[str, object] = {
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "episode_id": self.episode_id,
            "source_markdown": self.source_markdown,
            "profile_id": self.profile_id,
        }
        if self.episode_version_id is not None:
            value["episode_version_id"] = self.episode_version_id
        if self.prepared_content_key is not None:
            value["prepared_content_key"] = self.prepared_content_key
        if self.execution_mode is not None:
            value["execution_mode"] = self.execution_mode
        if self.max_attempts is not None:
            value["max_attempts"] = self.max_attempts
        return value


@dataclass(frozen=True, slots=True)
class PrepareContentActivityResult:
    """Deterministic preparation references suitable for workflow handoff."""

    tenant_id: str
    project_id: str
    episode_id: str
    source_sha256: str
    resolved_profile_id: str
    resolved_profile_version: str
    script_reference: Mapping[str, object]
    segment_references: tuple[Mapping[str, object], ...]
    critical_tokens: tuple[Mapping[str, object], ...]
    manifest_sha256: str
    provider_call_count: int
    render_workflow_input_json: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "project_id",
            "episode_id",
            "resolved_profile_id",
            "resolved_profile_version",
        ):
            _required_string(name, getattr(self, name))
        _required_sha256("source_sha256", self.source_sha256)
        _required_sha256("manifest_sha256", self.manifest_sha256)
        if type(self.provider_call_count) is not int or self.provider_call_count < 0:
            raise ValueError("provider_call_count must be non-negative")
        if self.render_workflow_input_json is not None:
            if not isinstance(self.render_workflow_input_json, str):
                raise ValueError("render_workflow_input_json must be a string")
            try:
                decoded = json.loads(self.render_workflow_input_json)
            except json.JSONDecodeError as error:
                raise ValueError(
                    "render_workflow_input_json must be valid JSON"
                ) from error
            if not isinstance(decoded, dict):
                raise ValueError("render_workflow_input_json must be an object")
        object.__setattr__(
            self, "script_reference", _frozen_reference(self.script_reference)
        )
        object.__setattr__(
            self,
            "segment_references",
            tuple(_frozen_reference(value) for value in self.segment_references),
        )
        object.__setattr__(
            self,
            "critical_tokens",
            tuple(_frozen_reference(value) for value in self.critical_tokens),
        )

    def to_dict(self) -> dict[str, object]:
        """Return JSON-native references without duplicating source bytes."""
        value: dict[str, object] = {
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "episode_id": self.episode_id,
            "source_sha256": self.source_sha256,
            "resolved_profile_id": self.resolved_profile_id,
            "resolved_profile_version": self.resolved_profile_version,
            "script_reference": _json_native_copy(self.script_reference),
            "segment_references": [
                _json_native_copy(value) for value in self.segment_references
            ],
            "critical_tokens": [
                _json_native_copy(value) for value in self.critical_tokens
            ],
            "manifest_sha256": self.manifest_sha256,
            "provider_call_count": self.provider_call_count,
        }
        if self.render_workflow_input_json is not None:
            value["render_workflow_input_json"] = self.render_workflow_input_json
        return value

    @classmethod
    def from_prepared(
        cls,
        value: PrepareContentActivityInput,
        prepared: ContentPreparationResult,
        *,
        render_workflow_input_json: str | None = None,
    ) -> PrepareContentActivityResult:
        """Project canonical preparation evidence into the activity contract."""
        if not isinstance(prepared, ContentPreparationResult):
            raise TypeError("typed preparation did not return canonical evidence")
        script = prepared.script
        provider_call_count = prepared.manifest.get("provider_calls", 0)
        if type(provider_call_count) is not int or provider_call_count < 0:
            raise ValueError("prepared provider call count is invalid")
        return cls(
            tenant_id=value.tenant_id,
            project_id=value.project_id,
            episode_id=value.episode_id,
            source_sha256=prepared.snapshot.source_sha256,
            resolved_profile_id=prepared.profile.profile_id,
            resolved_profile_version=prepared.profile.version,
            script_reference={
                "id": script.script_id,
                "canonical_hash": script.canonical_hash,
                "source_sha256": script.source_sha256,
            },
            segment_references=tuple(
                {
                    "segment_id": segment.segment_id,
                    "turn_ids": list(segment.turn_ids),
                    "speaker_ids": list(segment.speaker_ids),
                    "critical_token_ids": [
                        token.occurrence_id for token in segment.critical_tokens
                    ],
                    "estimated_duration_seconds": segment.estimated_duration_seconds,
                    "difficulty": segment.difficulty,
                }
                for segment in prepared.segments
            ),
            critical_tokens=tuple(
                {
                    "occurrence_id": token.occurrence_id,
                    "category": token.category,
                    "expected_spoken_form": token.expected_spoken_form,
                    "source_span": list(token.source_span),
                    "script_span": (
                        list(token.script_span)
                        if token.script_span is not None
                        else None
                    ),
                }
                for token in prepared.tokens
            ),
            manifest_sha256=prepared.manifest_sha256,
            provider_call_count=provider_call_count,
            render_workflow_input_json=render_workflow_input_json,
        )


def build_prepare_content_activity(
    request_factory: PreparationRequestFactory,
    render_snapshot_factory: RenderSnapshotFactory | None = None,
    live_adaptation_factory: LiveAdaptationFactory | None = None,
    prepared_content_recorder: PreparedContentRecorder | None = None,
) -> Callable[[PrepareContentActivityInput], PrepareContentActivityResult]:
    """Build a registered preparation activity from validated dependencies."""
    if not callable(request_factory):
        raise TypeError("request_factory must be callable")

    @activity.defn(name=PREPARE_CONTENT_ACTIVITY_NAME)
    def prepare(
        value: PrepareContentActivityInput,
    ) -> PrepareContentActivityResult:
        try:
            request = request_factory(value)
            if not isinstance(request, ContentPreparationRequest):
                raise TypeError("request factory returned invalid preparation request")
            if request.markdown != value.source_markdown:
                raise ValueError("request source does not match activity input")
            prepared: object
            if value.execution_mode == "live-provider":
                if live_adaptation_factory is None:
                    raise ValueError(
                        "live-provider preparation requires an explicit adaptation"
                    )
                adaptation = live_adaptation_factory(value)
                prepared = asyncio.run(prepare_content_live(request, adaptation))
            else:
                prepared = prepare_content(request)
            if not isinstance(prepared, ContentPreparationResult):
                raise TypeError("typed preparation did not return canonical evidence")
            if prepared.profile.profile_id != value.profile_id:
                raise ValueError("prepared profile does not match activity input")
            if prepared.snapshot.source != value.source_markdown:
                raise ValueError("prepared source does not match activity input")
            if prepared_content_recorder is not None:
                if value.prepared_content_key is None:
                    raise ValueError(
                        "prepared content persistence requires an identity key"
                    )
                prepared_content_recorder(value, prepared)
            render_workflow_input_json = (
                render_snapshot_factory(value, prepared)
                if render_snapshot_factory is not None
                else None
            )
            return PrepareContentActivityResult.from_prepared(
                value,
                prepared,
                render_workflow_input_json=render_workflow_input_json,
            )
        except (AdaptationError, LookupError, SegmentationError, TypeError, ValueError):
            raise ApplicationError(
                _PREPARATION_REJECTED_MESSAGE,
                type=_PREPARATION_REJECTED_TYPE,
                non_retryable=True,
            ) from None

    return prepare


def build_json_prepare_content_activity(
    request_factory: PreparationRequestFactory,
    render_snapshot_factory: RenderSnapshotFactory | None = None,
    live_adaptation_factory: LiveAdaptationFactory | None = None,
    prepared_content_recorder: PreparedContentRecorder | None = None,
) -> Callable[[PrepareContentActivityInput], dict[str, Any]]:
    """Build the Temporal wire adapter over the typed preparation activity.

    The canonical result intentionally freezes nested references with
    ``mappingproxy``. Temporal's default JSON converter cannot encode that
    implementation detail, so the worker boundary returns the result's
    explicit JSON projection while direct callers retain the typed result.
    """
    typed_activity = build_prepare_content_activity(
        request_factory,
        render_snapshot_factory,
        live_adaptation_factory,
        prepared_content_recorder,
    )

    @activity.defn(name=PREPARE_CONTENT_ACTIVITY_NAME)
    def prepare_json(
        value: PrepareContentActivityInput,
    ) -> dict[str, Any]:
        result = typed_activity(value)
        return result.to_dict()

    return prepare_json


__all__ = [
    "PREPARE_CONTENT_ACTIVITY_NAME",
    "PrepareContentActivityInput",
    "PrepareContentActivityResult",
    "PreparationRequestFactory",
    "RenderSnapshotFactory",
    "LiveAdaptationFactory",
    "PreparedContentRecorder",
    "build_json_prepare_content_activity",
    "build_prepare_content_activity",
]
