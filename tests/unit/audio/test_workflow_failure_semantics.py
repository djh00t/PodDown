"""Regression tests for terminal Temporal workflow failure decisions."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from temporalio.exceptions import ActivityError, ApplicationError

from poddown.audio.contracts import RenderRequest
from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.rights import VoiceConsent
from poddown.audio.selection import CandidateQuality
from poddown.audio.workflow import (
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    SegmentWorkflowInput,
    _failed_gates_for,
)
from poddown.domain import FidelityResult


def _input(*, max_attempts: int = 2) -> EpisodeWorkflowInput:
    segment = SegmentWorkflowInput(
        "segment-1",
        RenderRequest(
            "episode-1",
            "v1",
            "segment-1",
            "host",
            "Deterministic text.",
            "voice-host",
            "local",
            "local-v1",
        ),
        VoiceConsent("voice-host", "consent-1", frozenset({"local"})),
        (),
    )
    return EpisodeWorkflowInput("episode-1", "v1", (segment,), max_attempts)


def _activity_error(*, non_retryable: bool) -> ActivityError:
    error = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=1,
        identity="worker",
        activity_type="render",
        activity_id="activity-1",
        retry_state=None,
    )
    error.__cause__ = ApplicationError(
        "rights failed", type="RightsFailureError", non_retryable=non_retryable
    )
    return error


def _candidate(*, fidelity: bool, pronunciation: bool, audio: bool) -> CandidateQuality:
    return CandidateQuality(
        "candidate-1",
        FidelityResult(fidelity, 1.0 if fidelity else 0.0, "none"),
        AudioDiagnostics(44_100, 1, 1.0, 0.5 if audio else 1.1, 0.0, 0.1),
        pronunciation,
        Decimal("0.5"),
    )


def test_non_retryable_activity_failure_stops_segment_repair(monkeypatch):
    """A terminal Temporal activity error must not consume later repair attempts."""

    async def execute_activity(*args, **kwargs):
        del args, kwargs
        return _activity_error(non_retryable=True)

    monkeypatch.setattr(
        "poddown.audio.workflow.workflow.execute_activity", execute_activity
    )

    decision = asyncio.run(
        EpisodeRenderWorkflow()._run_segment(_input(), _input().segments[0])
    )

    assert decision.attempt == 1
    assert decision.failure_code == "RightsFailureError"


def test_malformed_candidate_output_becomes_terminal_decision(monkeypatch):
    """Bad activity dictionaries become structured terminal evidence, not exceptions."""

    async def execute_activity(*args, **kwargs):
        del args, kwargs
        return {"candidate_id": "missing required quality evidence"}

    monkeypatch.setattr(
        "poddown.audio.workflow.workflow.execute_activity", execute_activity
    )

    decision = asyncio.run(
        EpisodeRenderWorkflow()._run_segment(_input(), _input().segments[0])
    )

    assert decision.failure_code == "MALFORMED_ACTIVITY_OUTPUT"
    assert decision.accepted_candidate_id is None


def test_quality_failure_evidence_reports_only_failed_gates():
    """Terminal evidence must not claim gates that the candidate passed."""
    candidate = _candidate(fidelity=True, pronunciation=False, audio=True)

    assert _failed_gates_for("QUALITY_GATES_EXHAUSTED", (candidate,)) == (
        "pronunciation",
    )
