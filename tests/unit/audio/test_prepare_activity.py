"""Focused contract tests for the source preparation Temporal activity."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest
from temporalio.exceptions import ApplicationError

from poddown.audio.prepare_activity import (
    PREPARE_CONTENT_ACTIVITY_NAME,
    PrepareContentActivityInput,
    PrepareContentActivityResult,
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

_SOURCE = "LiDAR remains source-bound.\n"
_PROFILE = """\
profile_id: narration
version: v1
format_type: narration
target_minutes: 1
speakers:
  - speaker_id: host
    display_name: Host
    voice_asset_id: voice-host
"""


def _request(value: object = _SOURCE) -> ContentPreparationRequest:
    assert isinstance(value, str)
    snapshot = snapshot_source(value)
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
    turn = ScriptTurn("turn-1", "host", value.strip(), "factual", (anchor,), (anchor,))
    return ContentPreparationRequest(
        markdown=value,
        profile_yaml=_PROFILE,
        treatment=treatment,
        reasoning=FixtureReasoningPort(
            {snapshot.source_sha256: AdaptationProposal(treatment, (turn,))}, {}
        ),
        lexicon_layers={
            "episode": PronunciationLexicon(
                "episode",
                "v1",
                (PronunciationEntry("lidar", "LiDAR", "LIE-dar", "v1"),),
            )
        },
        capabilities=SegmentationCapabilities(100, None, frozenset({"host"})),
        voice_assets=(VoiceAsset("voice-host", True),),
        consents=(VoiceConsent("voice-host", True),),
    )


def test_prepare_activity_input_is_frozen_and_json_safe() -> None:
    value = PrepareContentActivityInput(
        "tenant", "project", "episode", "source", "profile"
    )
    with pytest.raises(FrozenInstanceError):
        value.profile_id = "other"  # type: ignore[misc]
    assert json.loads(json.dumps(value.to_dict())) == value.to_dict()


def test_prepare_activity_input_preserves_production_context() -> None:
    value = PrepareContentActivityInput(
        "tenant",
        "project",
        "episode",
        "source",
        "profile",
        episode_version_id="version-1",
        execution_mode="live-provider",
        max_attempts=3,
    )

    assert value.to_dict() == {
        "tenant_id": "tenant",
        "project_id": "project",
        "episode_id": "episode",
        "source_markdown": "source",
        "profile_id": "profile",
        "episode_version_id": "version-1",
        "execution_mode": "live-provider",
        "max_attempts": 3,
    }


def test_prepare_activity_result_freezes_nested_references() -> None:
    result = PrepareContentActivityResult(
        "tenant",
        "project",
        "episode",
        "a" * 64,
        "profile",
        "v1",
        {"id": "script", "spans": [[1, 2]]},
        ({"segment_id": "segment-1", "turn_ids": ["turn-1"]},),
        ({"occurrence_id": "token-1", "source_span": [1, 2]},),
        "b" * 64,
        0,
        render_workflow_input_json='{"episode_id":"episode"}',
    )
    payload = result.to_dict()
    payload["script_reference"]["spans"][0].append(3)  # type: ignore[index]
    assert result.script_reference["spans"] == ((1, 2),)
    assert PREPARE_CONTENT_ACTIVITY_NAME == "prepare_content"
    assert payload["render_workflow_input_json"] == '{"episode_id":"episode"}'


def test_prepare_activity_maps_factory_failures_without_source_leakage() -> None:
    value = PrepareContentActivityInput(
        "tenant", "project", "episode", "private", "profile"
    )

    def rejected(_value: PrepareContentActivityInput):
        raise ValueError("private source must not escape")

    with pytest.raises(ApplicationError) as error:
        build_prepare_content_activity(rejected)(value)
    assert error.value.type == "ContentPreparationRejectedError"
    assert error.value.non_retryable is True
    assert "private source" not in str(error.value)


def test_prepare_activity_requires_a_callable_factory() -> None:
    with pytest.raises(TypeError, match="request_factory"):
        build_prepare_content_activity(None)  # type: ignore[arg-type]


def test_prepare_activity_projects_worker_render_snapshot() -> None:
    value = PrepareContentActivityInput(
        "tenant", "project", "episode", _SOURCE, "narration"
    )

    result = build_prepare_content_activity(
        lambda _value: _request(),
        lambda _value, _prepared: '{"episode_id":"episode"}',
    )(value)

    assert result.render_workflow_input_json == '{"episode_id":"episode"}'


def test_live_prepare_activity_calls_only_the_explicit_live_adapter() -> None:
    value = PrepareContentActivityInput(
        "tenant",
        "project",
        "episode",
        _SOURCE,
        "narration",
        execution_mode="live-provider",
    )

    class Adapter:
        calls = 0

        async def adapt(self, source, profile, treatment):
            self.calls += 1
            anchor = treatment.source_anchors[0]
            from poddown.content.models import ScriptVersion

            return ScriptVersion(
                "provider-script",
                source.source_sha256,
                profile.profile_id,
                (
                    ScriptTurn(
                        "turn-1",
                        "host",
                        _SOURCE.strip(),
                        "factual",
                        (anchor,),
                        (anchor,),
                    ),
                ),
                "a" * 64,
            )

    adapter = Adapter()
    result = build_prepare_content_activity(
        lambda _value: _request(),
        live_adaptation_factory=lambda _value: adapter,
    )(value)

    assert adapter.calls == 1
    assert result.provider_call_count == 1
