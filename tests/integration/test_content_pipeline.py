"""Integration coverage for deterministic content preparation."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.models import ScriptTurn, SourceAnchor, VoiceAsset, VoiceConsent
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.source import snapshot_source


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "content"
SOURCE = (FIXTURES_DIR / "robotics-mapping.md").read_text(encoding="utf-8")
PROFILE_YAML = """profile_id: technical-dialogue
version: 1.0.0
format_type: dialogue
target_minutes: 12
speakers:
  - speaker_id: spk-archivist-1
    display_name: Archivist
    voice_asset_id: voice-archivist
  - speaker_id: spk-controls-2
    display_name: Controls
    voice_asset_id: voice-controls
style:
  tone: precise
audio: {}
quality:
  fidelity: strict
document_overridable: []
"""


def _anchor(snapshot, text: str) -> SourceAnchor:
    source_bytes = snapshot.source.encode("utf-8")
    start = source_bytes.index(text.encode("utf-8"))
    end = start + len(text.encode("utf-8"))
    block = next(
        block for block in snapshot.blocks if block.start <= start <= end <= block.end
    )
    return SourceAnchor(block.block_id, start, end)


def _request(*, changed_number: bool = False):
    """Build a complete, source-bound robotics request without provider access."""
    from poddown.content.service import ContentPreparationRequest

    snapshot = snapshot_source(SOURCE)
    overview = _anchor(
        snapshot,
        "On 2026-07-31 at 09:00 UTC, the mobile platform maps a 12-minute field pass.",
    )
    localization = _anchor(
        snapshot,
        "During the test window, EKF localization reports 99.7% positional consistency.",
    )
    controls = _anchor(
        snapshot,
        "The controller is not silent and it is not stable in high-frequency tests.",
    )
    disagreement = _anchor(
        snapshot,
        "I disagree with the earlier simplification that this architecture can split the "
        "render path",
    )
    treatment = EpisodeTreatment(
        "robotics-mapping",
        "dialogue",
        ("evidence", "challenge"),
        12,
        ("overview", "localization", "controls", "disagreement"),
        {"spk-archivist-1": "host", "spk-controls-2": "analyst"},
        (overview, localization, controls, disagreement),
        ("t-001", "t-002", "t-003", "t-004"),
    )
    turns = (
        ScriptTurn(
            "t-001",
            "spk-archivist-1",
            overview_text
            := "On 2026-07-31 at 09:00 UTC, the mobile platform maps a 12-minute field pass.",
            "factual",
            (overview,),
            (overview,),
        ),
        ScriptTurn(
            "t-002",
            "spk-controls-2",
            "During the test window, EKF localization reports "
            f"{'98.7%' if changed_number else '99.7%'} positional consistency.",
            "factual",
            (localization,),
            (localization,),
        ),
        ScriptTurn(
            "t-003",
            "spk-controls-2",
            "The controller is not silent and it is not stable in high-frequency tests.",
            "factual",
            (controls,),
            (controls,),
        ),
        ScriptTurn(
            "t-004",
            "spk-archivist-1",
            "I disagree with the earlier simplification that this architecture can split the render path",
            "factual",
            (disagreement,),
            (disagreement,),
        ),
    )
    del overview_text
    proposal = AdaptationProposal(treatment, turns)
    reasoning = FixtureReasoningPort({snapshot.source_sha256: proposal}, {})
    return ContentPreparationRequest(
        markdown=SOURCE,
        profile_yaml=PROFILE_YAML,
        treatment=treatment,
        reasoning=reasoning,
        lexicon_layers={},
        capabilities=SegmentationCapabilities(
            240, None, frozenset({"spk-archivist-1", "spk-controls-2"})
        ),
        voice_assets=(
            VoiceAsset("voice-archivist", True),
            VoiceAsset("voice-controls", True),
        ),
        consents=(
            VoiceConsent("voice-archivist", True),
            VoiceConsent("voice-controls", True),
        ),
    )


def test_prepare_content_builds_an_immutable_replayable_robotics_manifest():
    """Skipping a pipeline stage would remove source-bound replay evidence."""
    from poddown.content.service import canonical_manifest, prepare_content

    first = prepare_content(_request())
    second = prepare_content(_request())

    assert first == second
    assert first.snapshot.source_sha256 == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert tuple(turn.speaker_id for turn in first.script.turns) == (
        "spk-archivist-1",
        "spk-controls-2",
        "spk-controls-2",
        "spk-archivist-1",
    )
    assert all(turn.claim_anchors for turn in first.script.turns)
    assert tuple(
        turn_id for segment in first.segments for turn_id in segment.turn_ids
    ) == ("t-001", "t-002", "t-003", "t-004")
    assert (
        first.manifest_sha256
        == hashlib.sha256(
            canonical_manifest(first)["serialized"].encode("utf-8")
        ).hexdigest()
    )
    assert first.manifest["checksum"] == first.manifest_sha256
    assert first.manifest["provider_calls"] == 0
    with pytest.raises(FrozenInstanceError):
        first.manifest_sha256 = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.manifest["checksum"] = "changed"  # type: ignore[index]


def test_prepare_content_validates_profile_before_source_snapshotting():
    """Changing the order could leak malformed source failures before profile rejection."""
    from poddown.content.service import ContentPreparationRequest, prepare_content

    request = replace(
        _request(), markdown="---\nunterminated: true", profile_yaml="bad"
    )

    with pytest.raises(ValueError, match="Profile YAML must be an object"):
        prepare_content(request)


def test_prepare_content_propagates_safe_adaptation_failures_without_provider_calls():
    """Passing changed claims through would permit unsupported content to render."""
    from poddown.content.adaptation import AdaptationError
    from poddown.content.service import prepare_content

    with pytest.raises(AdaptationError) as error:
        prepare_content(_request(changed_number=True))

    assert error.value.code == "unsupported_claim"
    assert SOURCE not in str(error.value)
