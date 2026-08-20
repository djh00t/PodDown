"""BDD bindings for the source preparation Temporal activity."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from hashlib import sha256
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when
from temporalio.exceptions import ApplicationError

from poddown.audio.prepare_activity import (
    PrepareContentActivityInput,
    build_prepare_content_activity,
)
from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.models import ScriptTurn, SourceAnchor, VoiceAsset, VoiceConsent
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import ContentPreparationRequest
from poddown.content.source import snapshot_source

scenarios("../features/prepare_activity.feature")

SOURCE = "LiDAR remains source-bound.\n"
PROFILE_YAML = """\
profile_id: narration
version: v1
format_type: narration
target_minutes: 1
speakers:
  - speaker_id: host
    display_name: Host
    voice_asset_id: voice-host
"""


def _input(profile_id: str = "narration") -> PrepareContentActivityInput:
    return PrepareContentActivityInput(
        tenant_id="tenant-1",
        project_id="project-1",
        episode_id="episode-1",
        source_markdown=SOURCE,
        profile_id=profile_id,
    )


def _request(value: PrepareContentActivityInput) -> ContentPreparationRequest:
    snapshot = snapshot_source(value.source_markdown)
    block = snapshot.blocks[0]
    anchor = SourceAnchor(block.block_id, block.start, block.end)
    treatment = EpisodeTreatment(
        "treatment-1",
        "narration",
        ("source-bound",),
        1,
        ("reference",),
        {"host": "presenter"},
        (anchor,),
        ("turn-1",),
    )
    turns = (
        ScriptTurn(
            "turn-1",
            "host",
            SOURCE.strip(),
            "factual",
            (anchor,),
            (anchor,),
        ),
    )
    return ContentPreparationRequest(
        markdown=value.source_markdown,
        profile_yaml=PROFILE_YAML,
        treatment=treatment,
        reasoning=FixtureReasoningPort(
            {snapshot.source_sha256: AdaptationProposal(treatment, turns)}, {}
        ),
        lexicon_layers={
            "episode": PronunciationLexicon(
                "episode",
                "v1",
                (
                    PronunciationEntry(
                        "lidar",
                        "LiDAR",
                        "LIE-dar",
                        "v1",
                        category="technical_term",
                    ),
                ),
            )
        },
        capabilities=SegmentationCapabilities(100, None, frozenset({"host"})),
        voice_assets=(VoiceAsset("voice-host", True),),
        consents=(VoiceConsent("voice-host", True),),
    )


@given("a valid preparation activity input")
def valid_input(context: dict[str, Any]) -> None:
    context.values["input"] = _input()
    context.values["factory"] = _request


@given("a preparation activity input with a mismatched profile")
def mismatched_profile(context: dict[str, Any]) -> None:
    context.values["input"] = _input("interview")
    context.values["factory"] = _request


@when("the content preparation activity runs")
def run_activity(context: dict[str, Any]) -> None:
    try:
        context.values["result"] = build_prepare_content_activity(
            context.values["factory"]
        )(context.values["input"])
    except ApplicationError as error:
        context.values["error"] = error


@then("it returns source, profile, script, segment, and critical-token references")
def assert_references(context: dict[str, Any]) -> None:
    result = context.values["result"]
    assert result.source_sha256 == sha256(SOURCE.encode("utf-8")).hexdigest()
    assert result.resolved_profile_id == "narration"
    assert result.script_reference["id"]
    assert result.segment_references
    assert result.critical_tokens


@then("it records zero provider calls")
def assert_zero_provider_calls(context: dict[str, Any]) -> None:
    assert context.values["result"].provider_call_count == 0


@then("it fails with a non-retryable preparation validation error")
def assert_terminal_error(context: dict[str, Any]) -> None:
    error = context.values["error"]
    assert error.type == "ContentPreparationRejectedError"
    assert error.non_retryable is True
    assert error.message == "content preparation rejected"


@then("the preparation activity input snapshot is immutable and JSON-safe")
def assert_immutable_snapshot(context: dict[str, Any]) -> None:
    value = context.values["input"]
    with pytest.raises(FrozenInstanceError):
        value.profile_id = "other"
    assert json.loads(json.dumps(value.to_dict())) == value.to_dict()
