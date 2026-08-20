"""Strict, source-bound request contracts for structured reasoning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceSnapshot
from poddown.content.source import anchor_text

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ReasoningOperation = Literal["adapt", "repair"]


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _hash(name: str, value: object) -> str:
    value = _text(name, value)
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ReasoningRequest:
    """Immutable request data sent to a structured reasoning provider."""

    source_sha256: str
    profile_id: str
    treatment_id: str
    operation: ReasoningOperation
    source_blocks: tuple[dict[str, object], ...]
    speaker_ids: tuple[str, ...]
    repair_turn_id: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        _hash("source_sha256", self.source_sha256)
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
        """Return the exact JSON-safe request record."""
        record: dict[str, object] = {
            "schema_version": "1.0",
            "source_sha256": self.source_sha256,
            "profile_id": self.profile_id,
            "treatment_id": self.treatment_id,
            "operation": self.operation,
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
    """Build a request from verified source/profile/treatment snapshots."""
    if not isinstance(source, SourceSnapshot):
        raise TypeError("source must be SourceSnapshot")
    if not isinstance(profile, Profile):
        raise TypeError("profile must be Profile")
    if not isinstance(treatment, EpisodeTreatment):
        raise TypeError("treatment must be EpisodeTreatment")
    if treatment.format_type != profile.format_type:
        raise ValueError("treatment format does not match profile")
    for anchor in treatment.source_anchors:
        anchor_text(source, anchor)
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
    return ReasoningRequest(
        source_sha256=source.source_sha256,
        profile_id=profile.profile_id,
        treatment_id=treatment.treatment_id,
        operation=operation,
        source_blocks=blocks,
        speaker_ids=tuple(speaker.speaker_id for speaker in profile.speakers),
        repair_turn_id=repair_turn_id,
        failure_code=failure_code,
    )


def validate_reasoning_request_record(record: object) -> None:
    """Validate a serialized request without allowing unknown fields."""
    if not isinstance(record, dict):
        raise ValueError("reasoning request must be an object")
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


__all__ = [
    "ReasoningOperation",
    "ReasoningRequest",
    "build_reasoning_request",
    "validate_reasoning_request_record",
]
