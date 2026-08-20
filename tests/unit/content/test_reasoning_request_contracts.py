"""Unit coverage for strict source-bound reasoning request records."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any, cast

import pytest

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceAnchor, SourceSnapshot, SpeakerProfile
from poddown.content.reasoning_request import (
    ReasoningRequest,
    build_reasoning_request,
    validate_reasoning_request_record,
)
from poddown.content.source import snapshot_source

SOURCE = "# Topic\n\nThe system uses 44.1 kHz audio.\n"


def _request(
    *,
    operation: str = "adapt",
    source_blocks: object = ({"block_id": "b1"},),
    speaker_ids: object = ("host",),
    repair_turn_id: str | None = None,
    failure_code: str | None = None,
) -> ReasoningRequest:
    return ReasoningRequest(
        source_sha256="a" * 64,
        profile_id="profile",
        treatment_id="treatment",
        operation=operation,  # type: ignore[arg-type]
        source_blocks=source_blocks,  # type: ignore[arg-type]
        speaker_ids=speaker_ids,  # type: ignore[arg-type]
        repair_turn_id=repair_turn_id,
        failure_code=failure_code,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_sha256": "bad"},
        {"profile_id": " "},
        {"treatment_id": " "},
        {"operation": "unknown"},
        {"source_blocks": ()},
        {"source_blocks": ([],)},
        {"speaker_ids": ()},
        {"speaker_ids": ("host", "host")},
        {"speaker_ids": (" ",)},
        {"operation": "adapt", "repair_turn_id": "turn"},
        {
            "operation": "repair",
            "repair_turn_id": None,
            "failure_code": "dialogue_quality",
        },
        {"operation": "repair", "repair_turn_id": "turn", "failure_code": "other"},
    ],
)
def test_request_rejects_invalid_shapes(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        values: dict[str, object] = {
            "source_sha256": "a" * 64,
            "profile_id": "profile",
            "treatment_id": "treatment",
            "operation": "adapt",
            "source_blocks": ({"block_id": "b1"},),
            "speaker_ids": ("host",),
        }
        values.update(kwargs)
        ReasoningRequest(**values)  # type: ignore[arg-type]


def test_repair_request_record_contains_explicit_failure_identity() -> None:
    request = _request(
        operation="repair",
        repair_turn_id="turn-1",
        failure_code="dialogue_quality",
    )
    record = request.to_record()
    assert record["repair_turn_id"] == "turn-1"
    assert record["failure_code"] == "dialogue_quality"


def test_request_record_validation_is_canonical_and_fail_closed() -> None:
    record = _request().to_record()
    validate_reasoning_request_record(record)
    for invalid in (
        None,
        {"schema_version": "1.0"},
        {**record, "unknown": True},
        {**record, "schema_version": "2.0"},
        {**record, "source_blocks": ({"block_id": "b1"},)},
    ):
        with pytest.raises(ValueError):
            validate_reasoning_request_record(invalid)


def _profile() -> Profile:
    return Profile(
        profile_id="profile",
        version="1",
        format_type="dialogue",
        target_minutes=10,
        speakers=(
            SpeakerProfile("host", "Host", "voice-host"),
            SpeakerProfile("guest", "Guest", "voice-guest"),
        ),
        style={},
        audio={},
        quality={},
        document_overridable=frozenset(),
    )


def _treatment(
    source: SourceSnapshot,
    *,
    format_type: str = "dialogue",
) -> EpisodeTreatment:
    block = source.blocks[1]
    return EpisodeTreatment(
        treatment_id="treatment",
        format_type=cast(Any, format_type),
        narrative_arc=("overview",),
        target_minutes=10,
        sections=("overview",),
        speaker_roles={"host": "host"},
        source_anchors=(SourceAnchor(block.block_id, block.start, block.end),),
        expected_turn_ids=("turn-1",),
    )


def test_build_request_rejects_wrong_dependencies_and_format() -> None:
    source = snapshot_source(SOURCE)
    profile = _profile()
    treatment = _treatment(source)
    for values in (
        (object(), profile, treatment),
        (source, object(), treatment),
        (source, profile, object()),
    ):
        with pytest.raises(TypeError):
            build_reasoning_request(*values)
    with pytest.raises(ValueError, match="format"):
        build_reasoning_request(
            source, profile, replace(treatment, format_type="narration")
        )

    assert (
        build_reasoning_request(source, profile, treatment).source_sha256
        == sha256(SOURCE.encode()).hexdigest()
    )


def test_replacing_a_request_cannot_add_adaptation_repair_fields() -> None:
    with pytest.raises(ValueError):
        replace(_request(), failure_code="dialogue_quality")
