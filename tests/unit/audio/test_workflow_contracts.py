"""Contract tests for immutable workflow and activity identities."""

import asyncio
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from temporalio.exceptions import ApplicationError

from poddown.audio.contracts import RenderRequest
from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.rights import VoiceConsent
from poddown.audio.selection import CandidateQuality
from poddown.audio.workflow import (
    RENDER_SEGMENT_ACTIVITY_NAME,
    EpisodeWorkflowInput,
    EpisodeWorkflowResult,
    SegmentDecision,
    SegmentWorkflowInput,
    WorkflowContractError,
    WorkflowFailure,
    activity_key_for,
    render_segment_activity,
    workflow_id_for,
)
from poddown.domain import FidelityResult


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
    assert activity_key_for(
        first, "render", "segment-1", attempt=1, take=0
    ) == activity_key_for(second, "render", "segment-1", attempt=1, take=0)


@pytest.mark.parametrize(
    ("change", "different_argument"),
    [
        ("episode_version", {"episode_version": "v2"}),
        (
            "segment",
            {
                "segments": (
                    SegmentWorkflowInput(
                        "segment-2",
                        render_request(segment_id="segment-2"),
                        VoiceConsent(
                            "voice-host-v1", "consent-1", frozenset({"local"})
                        ),
                        ("deterministic",),
                    ),
                )
            },
        ),
    ],
)
def test_workflow_identity_changes_when_immutable_snapshot_changes(
    change, different_argument
):
    del change
    assert workflow_id_for(workflow_input()) != workflow_id_for(
        workflow_input(**different_argument)
    )


@pytest.mark.parametrize("field", ["attempt", "take"])
def test_activity_identity_changes_for_attempt_or_take(field: str):
    first = activity_key_for(workflow_input(), "render", "segment-1", attempt=1, take=0)
    changed = activity_key_for(
        workflow_input(),
        "render",
        "segment-1",
        attempt=2 if field == "attempt" else 1,
        take=1 if field == "take" else 0,
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


def test_workflow_result_round_trips_through_temporal_json_boundary():
    candidate = CandidateQuality(
        candidate_id="candidate-1",
        fidelity=FidelityResult(True, 1.0, "none"),
        diagnostics=AudioDiagnostics(44_100, 1, 1.0, 0.5, 0.0, 0.1),
        pronunciation_passed=True,
        soft_score=Decimal("0.80"),
    )
    result = EpisodeWorkflowResult(
        workflow_id="workflow-1",
        status="completed",
        decisions=(SegmentDecision("segment-1", 1, "candidate-1", (candidate,), None),),
        terminal_failure=None,
    )

    assert EpisodeWorkflowResult.from_json(result.to_json()) == result


def test_episode_input_round_trips_through_decoded_temporal_mapping():
    episode = workflow_input()

    assert EpisodeWorkflowInput.from_dict(episode.to_dict()) == episode


@pytest.mark.parametrize("payload", ["{", "[]", '{"segments": [{}]}'])
def test_malformed_temporal_input_fails_with_workflow_contract_error(payload: str):
    with pytest.raises(WorkflowContractError):
        EpisodeWorkflowInput.from_json(payload)


def test_segment_and_episode_contracts_reject_invalid_values():
    segment = workflow_input().segments[0]
    with pytest.raises(WorkflowContractError):
        SegmentWorkflowInput("", segment.render_request, segment.consent, ())
    with pytest.raises(WorkflowContractError):
        SegmentWorkflowInput("segment-2", segment.render_request, segment.consent, ())
    with pytest.raises(WorkflowContractError):
        SegmentWorkflowInput("segment-1", object(), segment.consent, ())  # type: ignore[arg-type]
    with pytest.raises(WorkflowContractError):
        SegmentWorkflowInput("segment-1", segment.render_request, object(), ())  # type: ignore[arg-type]
    with pytest.raises(WorkflowContractError):
        SegmentWorkflowInput(
            "segment-1", segment.render_request, segment.consent, ("",)
        )
    with pytest.raises(WorkflowContractError):
        EpisodeWorkflowInput("", "v1", (segment,))
    with pytest.raises(WorkflowContractError):
        EpisodeWorkflowInput("episode-1", "", (segment,))
    with pytest.raises(WorkflowContractError):
        EpisodeWorkflowInput("episode-1", "v1", [segment])  # type: ignore[arg-type]
    with pytest.raises(WorkflowContractError):
        EpisodeWorkflowInput("episode-1", "v1", (segment,), max_attempts=0)


def test_workflow_identity_boundaries_reject_invalid_arguments():
    with pytest.raises(TypeError):
        workflow_id_for(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        activity_key_for(object(), "render", "segment-1", attempt=1, take=0)  # type: ignore[arg-type]
    with pytest.raises(WorkflowContractError):
        activity_key_for(workflow_input(), "", "segment-1", attempt=1, take=0)
    with pytest.raises(WorkflowContractError):
        activity_key_for(workflow_input(), "render", "", attempt=1, take=0)
    with pytest.raises(WorkflowContractError):
        activity_key_for(workflow_input(), "render", "segment-1", attempt=0, take=0)
    with pytest.raises(WorkflowContractError):
        activity_key_for(workflow_input(), "render", "segment-1", attempt=1, take=-1)


def test_nested_temporal_result_boundaries_reject_malformed_values():
    with pytest.raises(WorkflowContractError):
        EpisodeWorkflowInput.from_dict(None)  # type: ignore[arg-type]
    with pytest.raises(WorkflowContractError):
        SegmentWorkflowInput.from_dict(None)  # type: ignore[arg-type]
    with pytest.raises(WorkflowContractError):
        SegmentWorkflowInput.from_dict({"render_request": {}, "consent": {}})
    with pytest.raises(WorkflowContractError):
        WorkflowFailure.from_dict(None)  # type: ignore[arg-type]
    with pytest.raises(WorkflowContractError):
        WorkflowFailure.from_dict({"failed_gates": []})
    with pytest.raises(WorkflowContractError):
        SegmentDecision.from_dict(None)  # type: ignore[arg-type]
    with pytest.raises(WorkflowContractError):
        SegmentDecision.from_dict({"candidates": []})
    for payload in ("{", "[]", '{"status":"unknown","decisions":[]}'):
        with pytest.raises(WorkflowContractError):
            EpisodeWorkflowResult.from_json(payload)


def test_failed_workflow_result_round_trips_terminal_failure():
    failure = WorkflowFailure(
        segment_id="segment-1",
        attempt_count=2,
        failed_gates=("audio",),
        last_error_code="QUALITY_GATES_EXHAUSTED",
    )
    result = EpisodeWorkflowResult("workflow-1", "failed", (), failure)

    assert EpisodeWorkflowResult.from_json(result.to_json()) == result


def test_workflow_result_rejects_invalid_terminal_failure_shape():
    with pytest.raises(WorkflowContractError, match="terminal_failure"):
        EpisodeWorkflowResult("workflow-1", "failed", (), object())  # type: ignore[arg-type]


def test_default_render_activity_fails_closed():
    with pytest.raises(ApplicationError, match="not configured") as error:
        asyncio.run(render_segment_activity({}))

    assert error.value.type == "ActivityNotConfigured"
    assert RENDER_SEGMENT_ACTIVITY_NAME == "poddown.audio.render_segment"
