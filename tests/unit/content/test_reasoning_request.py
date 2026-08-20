"""Unit coverage for deterministic reasoning-request construction."""

import json
import math

import pytest

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceAnchor, SourceSnapshot, SpeakerProfile
from poddown.content.source import snapshot_source


def _inputs() -> tuple[SourceSnapshot, Profile, EpisodeTreatment]:
    source = snapshot_source(
        "# Heading\n\nPublic figure: 42%.\n\nPrivate note: $9000.\n"
    )
    profile = Profile(
        profile_id="profile-001",
        version="2.0",
        format_type="dialogue",
        target_minutes=12,
        speakers=(
            SpeakerProfile("expert", "Expert", "voice-expert"),
            SpeakerProfile("host", "Host", "voice-host"),
        ),
        style={"tone": "plain"},
        audio={"ignored": "for-reasoning"},
        quality={"accuracy": "high"},
        document_overridable=frozenset(),
    )
    block = source.blocks[1]
    treatment = EpisodeTreatment(
        treatment_id="treatment-001",
        format_type="dialogue",
        narrative_arc=("opening",),
        target_minutes=12,
        sections=("public",),
        speaker_roles={"host": "host", "expert": "analyst"},
        source_anchors=(SourceAnchor(block.block_id, block.start, block.end),),
        expected_turn_ids=("turn-001",),
    )
    return source, profile, treatment


def test_build_reasoning_request_is_stable_serializable_and_source_minimized() -> None:
    """Keep requests source-minimized and stable for replay."""
    from poddown.content.reasoning_request import build_reasoning_request

    source, profile, treatment = _inputs()
    request = build_reasoning_request(source, profile, treatment)

    assert request.to_record() == {
        "schema_version": "1.0",
        "source_sha256": source.source_sha256,
        "treatment_id": "treatment-001",
        "profile": {
            "profile_id": "profile-001",
            "version": "2.0",
            "format_type": "dialogue",
            "target_minutes": 12,
            "style": {"tone": "plain"},
            "quality": {"accuracy": "high"},
            "speakers": [
                {"speaker_id": "expert", "display_name": "Expert"},
                {"speaker_id": "host", "display_name": "Host"},
            ],
        },
        "treatment": {
            "format_type": "dialogue",
            "target_minutes": 12,
            "narrative_arc": ["opening"],
            "sections": ["public"],
            "speaker_roles": [
                {"speaker_id": "expert", "role": "analyst"},
                {"speaker_id": "host", "role": "host"},
            ],
            "expected_turn_ids": ["turn-001"],
        },
        "source_anchors": [
            {
                "block_id": source.blocks[1].block_id,
                "start": source.blocks[1].start,
                "end": source.blocks[1].end,
                "text": "Public figure: 42%.",
                "critical_tokens": [
                    {
                        "occurrence_id": "tok-01",
                        "category": "percentage",
                        "normalized": "42%",
                        "source_form": "42%",
                        "start": source.blocks[1].start + 15,
                        "end": source.blocks[1].start + 18,
                        "expected_spoken_form": "forty-two percent",
                        "pronunciation_source": None,
                    }
                ],
            }
        ],
    }
    assert json.dumps(request.to_record(), allow_nan=False, sort_keys=True)
    assert "Private note" not in str(request.to_record())


def test_build_reasoning_request_rejects_unknown_profile_speakers() -> None:
    """A treatment cannot assign a role to an unapproved profile speaker."""
    from poddown.content.reasoning_request import build_reasoning_request

    source, profile, treatment = _inputs()
    invalid = EpisodeTreatment(
        treatment_id=treatment.treatment_id,
        format_type=treatment.format_type,
        narrative_arc=treatment.narrative_arc,
        target_minutes=treatment.target_minutes,
        sections=treatment.sections,
        speaker_roles={"guest": "guest"},
        source_anchors=treatment.source_anchors,
        expected_turn_ids=treatment.expected_turn_ids,
    )

    with pytest.raises(
        ValueError,
        match="treatment references unknown profile speaker IDs: \\['guest'\\]",
    ):
        build_reasoning_request(source, profile, invalid)


@pytest.mark.parametrize(
    ("format_type", "target_minutes"),
    [("narration", 12), ("dialogue", 11)],
)
def test_build_reasoning_request_rejects_profile_incompatible_treatments(
    format_type: str, target_minutes: int
) -> None:
    """A request is only built from a treatment matching the approved profile."""
    from poddown.content.reasoning_request import build_reasoning_request

    source, profile, treatment = _inputs()
    invalid = EpisodeTreatment(
        treatment_id=treatment.treatment_id,
        format_type=format_type,  # type: ignore[arg-type]
        narrative_arc=treatment.narrative_arc,
        target_minutes=target_minutes,
        sections=treatment.sections,
        speaker_roles=treatment.speaker_roles,
        source_anchors=treatment.source_anchors,
        expected_turn_ids=treatment.expected_turn_ids,
    )

    with pytest.raises(
        ValueError, match="treatment does not match the approved profile"
    ):
        build_reasoning_request(source, profile, invalid)


def test_build_reasoning_request_rejects_non_finite_metadata() -> None:
    """JSON-ready metadata cannot contain a non-finite float."""
    from poddown.content.reasoning_request import build_reasoning_request

    source, profile, treatment = _inputs()
    invalid_profile = Profile(
        profile_id=profile.profile_id,
        version=profile.version,
        format_type=profile.format_type,
        target_minutes=profile.target_minutes,
        speakers=profile.speakers,
        style={"score": math.nan},
        audio=profile.audio,
        quality=profile.quality,
        document_overridable=profile.document_overridable,
    )

    with pytest.raises(ValueError, match="finite floats"):
        build_reasoning_request(source, invalid_profile, treatment)


def test_reasoning_request_record_is_fresh_and_output_isolated() -> None:
    """Mutating one wire record cannot alter immutable request-domain state."""
    from poddown.content.reasoning_request import build_reasoning_request

    source, profile, treatment = _inputs()
    request = build_reasoning_request(source, profile, treatment)
    record = request.to_record()
    record["profile"]["style"]["tone"] = "changed"  # type: ignore[index]

    assert request.to_record()["profile"]["style"] == {"tone": "plain"}


def test_build_reasoning_request_rejects_an_invalid_treatment_anchor() -> None:
    """A request must not send text when its approved anchor cannot resolve."""
    from poddown.content.reasoning_request import build_reasoning_request

    source, profile, treatment = _inputs()
    invalid = EpisodeTreatment(
        treatment_id=treatment.treatment_id,
        format_type=treatment.format_type,
        narrative_arc=treatment.narrative_arc,
        target_minutes=treatment.target_minutes,
        sections=treatment.sections,
        speaker_roles=treatment.speaker_roles,
        source_anchors=(SourceAnchor("missing", 0, 1),),
        expected_turn_ids=treatment.expected_turn_ids,
    )

    with pytest.raises(ValueError, match="Unknown source block"):
        build_reasoning_request(source, profile, invalid)
