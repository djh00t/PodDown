"""Executable acceptance tests for deterministic Temporal episode orchestration."""

from importlib import import_module

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.audio.contracts import RenderRequest
from poddown.audio.rights import VoiceConsent

scenarios("../features/temporal_orchestration.feature")


def _render_request(segment_id: str) -> RenderRequest:
    """Return one stable local render request for a demo-mode segment."""
    return RenderRequest(
        episode_id="demo-temporal-episode",
        episode_version="v1",
        segment_id=segment_id,
        speaker_id="host",
        expected_spoken_text=f"Deterministic orchestration for {segment_id}.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )


def _orchestration_contracts():
    """Load the planned public boundary only when a scenario exercises it."""
    try:
        orchestration = import_module("poddown.audio.orchestration")
        workflow = import_module("poddown.audio.workflow")
    except ModuleNotFoundError as error:
        raise AssertionError(
            "Temporal episode orchestration is not implemented"
        ) from error
    return orchestration, workflow


def _run_episode(context):
    """Run the planned local application boundary with serializable demo fixtures."""
    orchestration, workflow = _orchestration_contracts()
    segment_inputs = tuple(
        workflow.SegmentWorkflowInput(
            segment_id=segment_id,
            render_request=_render_request(segment_id),
            consent=VoiceConsent(
                "voice-host-v1", "consent-demo-001", frozenset({"local"})
            ),
            critical_tokens=(segment_id,),
        )
        for segment_id in context.values["segment_ids"]
    )
    episode_input = workflow.EpisodeWorkflowInput(
        episode_id="demo-temporal-episode",
        episode_version="v1",
        segments=segment_inputs,
        max_attempts=2,
    )
    service = orchestration.LocalEpisodeWorkflowService(context.values["fixture"])
    context.values["result"] = service.run_episode(episode_input)
    context.values["episode_input"] = episode_input
    context.values["service"] = service


@pytest.fixture
def temporal_context(context):
    """Keep local fixture state isolated and explicitly marked as demo mode."""
    context.values["fixture"] = {
        "mode": "deterministic-local-demo",
        "attempts": {},
        "dispatches": [],
        "accepted_cost_events": [],
    }
    return context


@given("a deterministic two-segment episode with a three-take budget")
def deterministic_two_segment_episode(temporal_context):
    temporal_context.values["segment_ids"] = ("segment-001", "segment-002")
    temporal_context.values["fixture"]["attempts"] = {
        "segment-001": [["take-0", "take-1", "take-2"]],
        "segment-002": [["take-0", "take-1", "take-2"]],
    }


@given("a segment with a high-score candidate that fails a hard gate")
def high_score_hard_gate_failure(temporal_context):
    temporal_context.values["segment_ids"] = ("segment-001",)
    temporal_context.values["fixture"]["attempts"] = {
        "segment-001": [
            [
                {
                    "candidate_id": "candidate-failed-fidelity",
                    "fidelity": {"passed": False, "accuracy": 0.0},
                    "diagnostics": {"passed": True},
                    "pronunciation_passed": True,
                    "soft_score": "0.99",
                },
            ]
        ]
    }


@given("a lower-score candidate that passes every hard gate")
def lower_score_hard_gate_success(temporal_context):
    temporal_context.values["fixture"]["attempts"]["segment-001"][0].append(
        {
            "candidate_id": "candidate-passed-gates",
            "fidelity": {"passed": True, "accuracy": 1.0},
            "diagnostics": {"passed": True},
            "pronunciation_passed": True,
            "soft_score": "0.10",
        }
    )


@given("a segment whose first render activity fails transiently")
def transient_first_render_failure(temporal_context):
    temporal_context.values["segment_ids"] = ("segment-001",)
    temporal_context.values["fixture"]["attempts"] = {
        "segment-001": [
            {"error_code": "transient-render-unavailable"},
            ["take-0", "take-1", "take-2"],
        ]
    }


@given("a two-segment episode with one accepted segment and one failed segment")
def partially_accepted_episode(temporal_context):
    temporal_context.values["segment_ids"] = ("segment-001", "segment-002")
    temporal_context.values["fixture"]["attempts"] = {
        "segment-001": [["take-0", "take-1", "take-2"]],
        "segment-002": [
            {"error_code": "hard-gate-failed"},
            ["take-0", "take-1", "take-2"],
        ],
    }


@given("a completed deterministic episode workflow")
def completed_deterministic_workflow(temporal_context):
    temporal_context.values["segment_ids"] = ("segment-001",)
    temporal_context.values["fixture"]["attempts"] = {
        "segment-001": [["take-0", "take-1", "take-2"]],
    }
    _run_episode(temporal_context)
    temporal_context.values["first_result"] = temporal_context.values["result"]
    temporal_context.values["dispatch_count"] = len(
        temporal_context.values["fixture"]["dispatches"]
    )
    temporal_context.values["accepted_cost_event_count"] = len(
        temporal_context.values["fixture"]["accepted_cost_events"]
    )


@when("the local episode workflow is run")
def local_episode_workflow_run(temporal_context):
    _run_episode(temporal_context)


@when("the same immutable episode input is run again")
def same_immutable_episode_run_again(temporal_context):
    service = temporal_context.values["service"]
    temporal_context.values["result"] = service.run_episode(
        temporal_context.values["episode_input"]
    )


@then("every segment has exactly three candidates in take order")
def every_segment_has_three_ordered_candidates(temporal_context):
    decisions = temporal_context.values["result"].decisions
    assert len(decisions) == 2
    for decision in decisions:
        assert [candidate.candidate_id for candidate in decision.candidates] == [
            f"{decision.segment_id}-take-0",
            f"{decision.segment_id}-take-1",
            f"{decision.segment_id}-take-2",
        ]


@then("no segment receives more than three takes in an attempt")
def no_segment_receives_more_than_three_takes(temporal_context):
    for dispatch in temporal_context.values["fixture"]["dispatches"]:
        assert dispatch["take_index"] in {0, 1, 2}
    assert len(temporal_context.values["fixture"]["dispatches"]) == 6


@then("the hard-gate-passing candidate is accepted")
def hard_gate_passing_candidate_accepted(temporal_context):
    (decision,) = temporal_context.values["result"].decisions
    assert decision.accepted_candidate_id == "candidate-passed-gates"


@then("the hard-gate-failing candidate is not accepted")
def hard_gate_failing_candidate_not_accepted(temporal_context):
    (decision,) = temporal_context.values["result"].decisions
    assert decision.accepted_candidate_id != "candidate-failed-fidelity"


@then("the segment succeeds on its second attempt")
def segment_succeeds_on_second_attempt(temporal_context):
    (decision,) = temporal_context.values["result"].decisions
    assert decision.attempt == 2
    assert decision.accepted_candidate_id is not None


@then("exactly one accepted cost event is recorded for the segment")
def exactly_one_accepted_cost_event(temporal_context):
    events = temporal_context.values["fixture"]["accepted_cost_events"]
    assert len(events) == 1
    assert events[0]["segment_id"] == "segment-001"


@then("the accepted segment is not dispatched for repair")
def accepted_segment_not_dispatched_for_repair(temporal_context):
    dispatches = temporal_context.values["fixture"]["dispatches"]
    assert [
        dispatch["attempt"]
        for dispatch in dispatches
        if dispatch["segment_id"] == "segment-001"
    ] == [1, 1, 1]


@then("the failed segment is dispatched for its second attempt only")
def failed_segment_dispatched_for_second_attempt(temporal_context):
    dispatches = temporal_context.values["fixture"]["dispatches"]
    assert [
        dispatch["attempt"]
        for dispatch in dispatches
        if dispatch["segment_id"] == "segment-002"
    ] == [2, 2, 2]


@then("the existing completed workflow result is returned")
def completed_workflow_result_returned(temporal_context):
    assert temporal_context.values["result"] == temporal_context.values["first_result"]


@then("replay records no additional dispatches or accepted cost events")
def replay_records_no_additional_dispatch_or_cost(temporal_context):
    fixture = temporal_context.values["fixture"]
    assert len(fixture["dispatches"]) == temporal_context.values["dispatch_count"]
    assert (
        len(fixture["accepted_cost_events"])
        == temporal_context.values["accepted_cost_event_count"]
    )
