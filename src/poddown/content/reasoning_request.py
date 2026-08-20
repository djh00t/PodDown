"""Strict, source-bound request contracts for structured reasoning."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceAnchor, SourceSnapshot
from poddown.content.source import anchor_text
from poddown.content.tokens import CriticalToken, extract_critical_tokens

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ReasoningOperation = Literal["adapt", "repair"]
_SCHEMA_VERSION = "1.0"
_LEGACY_FIELDS = frozenset(
    {
        "schema_version",
        "source_sha256",
        "treatment_id",
        "profile",
        "treatment",
        "source_anchors",
    }
)

type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _hash(name: str, value: object) -> str:
    value = _text(name, value)
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True, init=False)
class ReasoningRequest:
    """Immutable request data supporting both preserved reasoning wire shapes.

    The first P1 reasoning contract carries approved profile, treatment, and
    source-anchor records.  Later candidate-evaluation work added a compact
    operation/source-block form.  Both remain useful persisted contracts, so
    the constructor and validator retain each shape explicitly.
    """

    source_sha256: str
    profile_id: str | None = field(default=None, init=True)
    treatment_id: str | None = field(default=None, init=True)
    operation: ReasoningOperation | None = field(default=None, init=True)
    source_blocks: tuple[dict[str, object], ...] = field(default=(), init=True)
    speaker_ids: tuple[str, ...] = field(default=(), init=True)
    repair_turn_id: str | None = None
    failure_code: str | None = None
    profile: dict[str, JsonValue] | None = field(default=None, init=True, repr=False)
    treatment: dict[str, JsonValue] | None = field(default=None, init=True, repr=False)
    source_anchors: tuple[dict[str, JsonValue], ...] | None = field(
        default=None, init=True, repr=False
    )
    _wire_format: Literal["advanced", "legacy"] = field(
        default="advanced", init=True, repr=False
    )

    def __init__(
        self,
        source_sha256: str,
        profile_id: str | None = None,
        treatment_id: str | None = None,
        operation: ReasoningOperation | None = None,
        source_blocks: tuple[dict[str, object], ...] = (),
        speaker_ids: tuple[str, ...] = (),
        repair_turn_id: str | None = None,
        failure_code: str | None = None,
        *,
        profile: dict[str, JsonValue] | None = None,
        treatment: dict[str, JsonValue] | None = None,
        source_anchors: tuple[dict[str, JsonValue], ...] | None = None,
        _wire_format: Literal["advanced", "legacy", "auto"] = "auto",
    ) -> None:
        legacy = _wire_format == "legacy" or (
            _wire_format == "auto"
            and (
                profile is not None
                or treatment is not None
                or source_anchors is not None
            )
        )
        if _wire_format not in {"advanced", "legacy", "auto"}:
            raise ValueError("reasoning request wire format is unsupported")
        if _wire_format == "advanced" and (
            profile is not None or treatment is not None or source_anchors is not None
        ):
            raise ValueError("advanced reasoning requests cannot carry legacy fields")
        object.__setattr__(self, "source_sha256", source_sha256)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "treatment_id", treatment_id)
        object.__setattr__(self, "operation", None if legacy else operation)
        object.__setattr__(self, "source_blocks", tuple(source_blocks))
        object.__setattr__(self, "speaker_ids", tuple(speaker_ids))
        object.__setattr__(self, "repair_turn_id", repair_turn_id)
        object.__setattr__(self, "failure_code", failure_code)
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "treatment", treatment)
        object.__setattr__(self, "source_anchors", source_anchors)
        object.__setattr__(self, "_wire_format", "legacy" if legacy else "advanced")
        self.__post_init__()

    def __post_init__(self) -> None:
        _hash("source_sha256", self.source_sha256)
        if self._wire_format == "legacy":
            _text("treatment_id", self.treatment_id)
            if not isinstance(self.profile, dict) or not isinstance(
                self.treatment, dict
            ):
                raise ValueError("legacy reasoning request metadata must be mappings")
            if not isinstance(self.source_anchors, tuple) or not all(
                isinstance(anchor, dict) for anchor in self.source_anchors
            ):
                raise ValueError("source_anchors must contain mappings")
            return

        _text("profile_id", self.profile_id)
        _text("treatment_id", self.treatment_id)
        if self.operation not in {"adapt", "repair"}:
            raise ValueError("operation must be adapt or repair")
        if not isinstance(self.source_blocks, tuple) or not self.source_blocks:
            raise ValueError("source_blocks must be a non-empty tuple")
        if not all(isinstance(block, dict) for block in self.source_blocks):
            raise ValueError("source_blocks must contain mappings")
        if not isinstance(self.speaker_ids, tuple) or not self.speaker_ids:
            raise ValueError("speaker_ids must be a non-empty tuple")
        if len(set(self.speaker_ids)) != len(self.speaker_ids) or any(
            not isinstance(speaker, str) or not speaker.strip()
            for speaker in self.speaker_ids
        ):
            raise ValueError("speaker_ids must be unique non-empty strings")
        if self.operation == "adapt":
            if self.repair_turn_id is not None or self.failure_code is not None:
                raise ValueError("adapt requests cannot carry repair fields")
        else:
            _text("repair_turn_id", self.repair_turn_id)
            if self.failure_code != "dialogue_quality":
                raise ValueError("repair requests require dialogue_quality")

    def to_record(self) -> dict[str, object]:
        """Return the exact JSON-safe record for this request shape."""
        if self._wire_format == "legacy":
            return {
                "schema_version": _SCHEMA_VERSION,
                "source_sha256": self.source_sha256,
                "treatment_id": cast(str, self.treatment_id),
                "profile": _copy_json_mapping(cast(dict[str, JsonValue], self.profile)),
                "treatment": _copy_json_mapping(
                    cast(dict[str, JsonValue], self.treatment)
                ),
                "source_anchors": [
                    _copy_json_mapping(anchor)
                    for anchor in cast(
                        tuple[dict[str, JsonValue], ...], self.source_anchors
                    )
                ],
            }
        record: dict[str, object] = {
            "schema_version": "1.0",
            "source_sha256": self.source_sha256,
            "profile_id": cast(str, self.profile_id),
            "treatment_id": cast(str, self.treatment_id),
            "operation": cast(ReasoningOperation, self.operation),
            "source_blocks": [dict(block) for block in self.source_blocks],
            "speaker_ids": list(self.speaker_ids),
        }
        if self.operation == "repair":
            record["repair_turn_id"] = self.repair_turn_id
            record["failure_code"] = self.failure_code
        return record


def build_reasoning_request(
    source: SourceSnapshot,
    profile: Profile,
    treatment: EpisodeTreatment,
    *,
    operation: ReasoningOperation = "adapt",
    repair_turn_id: str | None = None,
    failure_code: str | None = None,
) -> ReasoningRequest:
    """Build the source-bound request from verified snapshots.

    The established public builder emits the versioned profile/treatment
    contract.  The compact operation form remains available through the
    explicit constructor for later reasoning-candidate consumers.
    """
    if not isinstance(source, SourceSnapshot):
        raise TypeError("source must be SourceSnapshot")
    if not isinstance(profile, Profile):
        raise TypeError("profile must be Profile")
    if not isinstance(treatment, EpisodeTreatment):
        raise TypeError("treatment must be EpisodeTreatment")
    if (
        treatment.format_type != profile.format_type
        or treatment.target_minutes != profile.target_minutes
    ):
        raise ValueError(
            "treatment format does not match the approved profile; "
            "treatment does not match the approved profile"
        )
    unknown_speaker_ids = sorted(
        set(treatment.speaker_roles)
        - {speaker.speaker_id for speaker in profile.speakers}
    )
    if unknown_speaker_ids:
        raise ValueError(
            f"treatment references unknown profile speaker IDs: {unknown_speaker_ids}"
        )

    blocks = tuple(
        {
            "block_id": block.block_id,
            "kind": block.kind,
            "text": block.text,
            "start": block.start,
            "end": block.end,
        }
        for block in source.blocks
    )
    speaker_ids = tuple(speaker.speaker_id for speaker in profile.speakers)

    if operation != "adapt" or repair_turn_id is not None or failure_code is not None:
        return ReasoningRequest(
            source_sha256=source.source_sha256,
            profile_id=profile.profile_id,
            treatment_id=treatment.treatment_id,
            operation=operation,
            source_blocks=blocks,
            speaker_ids=speaker_ids,
            repair_turn_id=repair_turn_id,
            failure_code=failure_code,
        )

    return ReasoningRequest(
        source_sha256=source.source_sha256,
        profile_id=profile.profile_id,
        treatment_id=treatment.treatment_id,
        source_blocks=blocks,
        speaker_ids=speaker_ids,
        profile=_profile_record(profile),
        treatment=_treatment_record(treatment),
        source_anchors=tuple(
            _anchor_record(source, anchor) for anchor in treatment.source_anchors
        ),
        _wire_format="legacy",
    )


def validate_reasoning_request_record(record: object) -> None:
    """Validate either preserved reasoning request wire contract."""
    if not isinstance(record, dict):
        raise ValueError("reasoning request must be an object")
    if set(record) == _LEGACY_FIELDS:
        _validate_legacy_record(record)
        return
    required = {
        "schema_version",
        "source_sha256",
        "profile_id",
        "treatment_id",
        "operation",
        "source_blocks",
        "speaker_ids",
    }
    if not required <= set(record):
        raise ValueError("reasoning request is missing required fields")
    allowed = required | {"repair_turn_id", "failure_code"}
    if set(record) - allowed:
        raise ValueError("reasoning request contains unknown fields")
    if record["schema_version"] != "1.0":
        raise ValueError("reasoning request schema version is unsupported")
    request = ReasoningRequest(
        source_sha256=record["source_sha256"],
        profile_id=record["profile_id"],
        treatment_id=record["treatment_id"],
        operation=record["operation"],
        source_blocks=tuple(record["source_blocks"]),
        speaker_ids=tuple(record["speaker_ids"]),
        repair_turn_id=record.get("repair_turn_id"),
        failure_code=record.get("failure_code"),
    )
    if request.to_record() != record:
        raise ValueError("reasoning request is not canonical")


def _validate_legacy_record(record: dict[str, object]) -> None:
    """Validate the established profile/treatment/source-anchor record."""
    try:
        Draft202012Validator(_reasoning_request_schema()).validate(record)
    except ValidationError as error:
        raise ValueError("reasoning request record does not satisfy schema") from error
    anchors = record["source_anchors"]
    if not isinstance(anchors, list):
        raise ValueError("reasoning request record does not satisfy schema")
    for anchor in anchors:
        if not isinstance(anchor, dict):
            raise ValueError("reasoning request record does not satisfy schema")
        _validate_ordered_span(anchor, "source anchor", allow_empty=True)
        tokens = anchor["critical_tokens"]
        if not isinstance(tokens, list):
            raise ValueError("reasoning request record does not satisfy schema")
        for token in tokens:
            if not isinstance(token, dict):
                raise ValueError("reasoning request record does not satisfy schema")
            _validate_ordered_span(token, "critical token", allow_empty=False)


def _reasoning_request_schema() -> dict[str, object]:
    schema_path = (
        Path(__file__).parents[3]
        / "specs/003-durable-audio-production/contracts/reasoning-request.schema.json"
    )
    return cast(dict[str, object], json.loads(schema_path.read_text()))


def _validate_ordered_span(
    record: Mapping[str, object], label: str, *, allow_empty: bool
) -> None:
    start = record["start"]
    end = record["end"]
    if type(start) is not int or type(end) is not int:
        raise ValueError("reasoning request record does not satisfy schema")
    if end < start or (not allow_empty and end == start):
        raise ValueError(f"{label} start must not exceed end")


def _profile_record(profile: Profile) -> dict[str, JsonValue]:
    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "format_type": profile.format_type,
        "target_minutes": profile.target_minutes,
        "style": _json_mapping(profile.style),
        "quality": _json_mapping(profile.quality),
        "speakers": [
            {
                "speaker_id": speaker.speaker_id,
                "display_name": speaker.display_name,
            }
            for speaker in profile.speakers
        ],
    }


def _treatment_record(treatment: EpisodeTreatment) -> dict[str, JsonValue]:
    return {
        "format_type": treatment.format_type,
        "target_minutes": treatment.target_minutes,
        "narrative_arc": list(treatment.narrative_arc),
        "sections": list(treatment.sections),
        "speaker_roles": [
            {"speaker_id": speaker_id, "role": role}
            for speaker_id, role in sorted(treatment.speaker_roles.items())
        ],
        "expected_turn_ids": list(treatment.expected_turn_ids),
    }


def _anchor_record(
    source: SourceSnapshot, anchor: SourceAnchor
) -> dict[str, JsonValue]:
    text = anchor_text(source, anchor)
    return {
        "block_id": anchor.block_id,
        "start": anchor.start,
        "end": anchor.end,
        "text": text,
        "critical_tokens": [
            _token_record(token, anchor.start)
            for token in extract_critical_tokens(text)
        ],
    }


def _token_record(token: CriticalToken, offset: int) -> dict[str, JsonValue]:
    start, end = token.source_span
    return {
        "occurrence_id": token.occurrence_id,
        "category": token.category,
        "normalized": token.normalized,
        "source_form": token.source_form,
        "start": offset + start,
        "end": offset + end,
        "expected_spoken_form": token.expected_spoken_form,
        "pronunciation_source": token.pronunciation_source,
    }


def _json_mapping(value: Mapping[str, object]) -> dict[str, JsonValue]:
    return {key: _json_value(item) for key, item in sorted(value.items())}


def _json_value(value: object) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("reasoning request metadata must contain finite floats")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("reasoning request metadata keys must be strings")
        return _json_mapping(value)
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    raise ValueError("reasoning request metadata must be JSON-compatible")


def _copy_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    return value


def _copy_json_mapping(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: _copy_json(item) for key, item in value.items()}


__all__ = [
    "ReasoningOperation",
    "ReasoningRequest",
    "build_reasoning_request",
    "validate_reasoning_request_record",
]
