"""BDD bindings for strict source-bound reasoning requests."""

from __future__ import annotations

from hashlib import sha256

import pytest

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceAnchor, SpeakerProfile
from poddown.content.reasoning_request import build_reasoning_request
from poddown.content.source import snapshot_source

SOURCE = "# Topic\n\nThe system uses 44.1 kHz audio.\n"


def _profile() -> Profile:
    return Profile(
        profile_id="technical-dialogue",
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


def _treatment(source):
    return EpisodeTreatment(
        treatment_id="treatment-1",
        format_type="dialogue",
        narrative_arc=("overview",),
        target_minutes=10,
        sections=("overview",),
        speaker_roles={"host": "host", "guest": "guest"},
        source_anchors=(
            SourceAnchor(
                source.blocks[1].block_id, source.blocks[1].start, source.blocks[1].end
            ),
        ),
        expected_turn_ids=("turn-1",),
    )


def test_build_reasoning_request_preserves_source_identity() -> None:
    source = snapshot_source(SOURCE)
    request = build_reasoning_request(source, _profile(), _treatment(source))
    record = request.to_record()
    assert record["source_sha256"] == sha256(SOURCE.encode()).hexdigest()
    assert record["treatment_id"] == "treatment-1"
    assert "credential" not in repr(record).casefold()


def test_reasoning_request_rejects_source_anchor_outside_snapshot() -> None:
    source = snapshot_source(SOURCE)
    treatment = _treatment(source)
    bad = EpisodeTreatment(
        treatment_id=treatment.treatment_id,
        format_type=treatment.format_type,
        narrative_arc=treatment.narrative_arc,
        target_minutes=treatment.target_minutes,
        sections=treatment.sections,
        speaker_roles=treatment.speaker_roles,
        source_anchors=(
            SourceAnchor(
                source.blocks[1].block_id,
                source.blocks[1].start,
                source.blocks[1].end + 1,
            ),
        ),
        expected_turn_ids=treatment.expected_turn_ids,
    )
    with pytest.raises(ValueError, match="anchor"):
        build_reasoning_request(source, _profile(), bad)
