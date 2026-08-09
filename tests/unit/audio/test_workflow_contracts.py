"""Contract tests for immutable workflow and activity identities."""

from dataclasses import FrozenInstanceError

import pytest

from poddown.audio.contracts import RenderRequest
from poddown.audio.rights import VoiceConsent
from poddown.audio.workflow import (
    EpisodeWorkflowInput,
    SegmentWorkflowInput,
    WorkflowFailure,
    activity_key_for,
    workflow_id_for,
)


def render_request(**overrides: object) -> RenderRequest:
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "episode_version": "v1",
        "segment_id": "segment-1",
        "speaker_id": "host",
        "expected_spoken_text": "A deterministic segment.",
        "voice_asset_id": "voice-host-v1",
        "provider": "local",
        "model": "local-deterministic-v1",
    }
    values.update(overrides)
    return RenderRequest(**values)  # type: ignore[arg-type]


def workflow_input(**overrides: object) -> EpisodeWorkflowInput:
    segment = SegmentWorkflowInput(
        segment_id="segment-1",
        render_request=render_request(),
        consent=VoiceConsent("voice-host-v1", "consent-1", frozenset({"local"})),
        critical_tokens=("deterministic",),
    )
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "episode_version": "v1",
        "segments": (segment,),
    }
    values.update(overrides)
    return EpisodeWorkflowInput(**values)  # type: ignore[arg-type]


def test_equal_workflow_inputs_have_stable_workflow_and_activity_identities():
    first = workflow_input()
    second = workflow_input()

    assert workflow_id_for(first) == workflow_id_for(second)
    assert activity_key_for(first, "render", "segment-1", attempt=1, take=0) == activity_key_for(
        second, "render", "segment-1", attempt=1, take=0
    )


@pytest.mark.parametrize(
    ("change", "different_argument"),
    [
        ("episode_version", {"episode_version": "v2"}),
        ("segment", {"segments": (SegmentWorkflowInput(
            "segment-2", render_request(segment_id="segment-2"),
            VoiceConsent("voice-host-v1", "consent-1", frozenset({"local"})), ("deterministic",)
        ),)}),
    ],
)
def test_workflow_identity_changes_when_immutable_snapshot_changes(change, different_argument):
    del change
    assert workflow_id_for(workflow_input()) != workflow_id_for(workflow_input(**different_argument))


@pytest.mark.parametrize("field", ["attempt", "take"])
def test_activity_identity_changes_for_attempt_or_take(field: str):
    first = activity_key_for(workflow_input(), "render", "segment-1", attempt=1, take=0)
    changed = activity_key_for(
        workflow_input(), "render", "segment-1", attempt=2 if field == "attempt" else 1, take=1 if field == "take" else 0
    )

    assert first != changed


def test_workflow_contracts_are_frozen_and_terminal_failure_is_structured():
    with pytest.raises(FrozenInstanceError):
        workflow_input().episode_id = "changed"  # type: ignore[misc]

    failure = WorkflowFailure(
        segment_id="segment-1",
        attempt_count=2,
        failed_gates=("pronunciation", "fidelity"),
        last_error_code="QUALITY_GATES_EXHAUSTED",
    )

    assert failure.to_dict() == {
        "attempt_count": 2,
        "failed_gates": ["pronunciation", "fidelity"],
        "last_error_code": "QUALITY_GATES_EXHAUSTED",
        "segment_id": "segment-1",
    }
