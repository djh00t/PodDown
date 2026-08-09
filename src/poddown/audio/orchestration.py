"""Local and Temporal application services for immutable episode workflows."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.selection import CandidateQuality, select_candidate
from poddown.audio.workflow import (
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    EpisodeWorkflowResult,
    SegmentDecision,
    WorkflowContractError,
    WorkflowFailure,
    workflow_id_for,
)
from poddown.domain import FidelityResult


class LocalOrchestrationError(ValueError):
    """Raised when the deterministic demo fixture is malformed."""


class TemporalEpisodeWorkflowService:
    """Run or reuse immutable episode workflows through Temporal."""

    def __init__(self, client: Client, task_queue: str) -> None:
        if not isinstance(task_queue, str) or not task_queue:
            raise ValueError("task_queue must be a non-empty string")
        self._client = client
        self._task_queue = task_queue

    async def run_episode(
        self, workflow_input: EpisodeWorkflowInput
    ) -> EpisodeWorkflowResult:
        """Start one snapshot or await its existing Temporal workflow."""
        workflow_id = workflow_id_for(workflow_input)
        try:
            handle = await self._client.start_workflow(
                EpisodeRenderWorkflow.run,
                workflow_input.to_json(),
                id=workflow_id,
                task_queue=self._task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            # A concurrent caller or prior completed run owns this immutable ID.
            handle = self._client.get_workflow_handle_for(
                EpisodeRenderWorkflow.run, workflow_id
            )

        result_json = await handle.result()
        if not isinstance(result_json, str):
            raise WorkflowContractError("episode workflow result must be JSON text")
        return EpisodeWorkflowResult.from_json(result_json)


_PASSING_DIAGNOSTICS = AudioDiagnostics(
    sample_rate_hz=44_100,
    channels=1,
    duration_seconds=1.0,
    peak_amplitude=0.5,
    clipping_ratio=0.0,
    silence_ratio=0.1,
)


class LocalEpisodeWorkflowService:
    """Run the workflow contract against an explicit deterministic fixture.

    This service is intentionally a local demo adapter. It consumes planned
    activity outcomes, records dispatch/cost evidence in the supplied fixture,
    and never contacts a provider. The Temporal-backed service uses the same
    workflow contracts and selection rules at the application boundary.
    """

    def __init__(self, fixture: dict[str, Any]) -> None:
        if not isinstance(fixture, dict):
            raise LocalOrchestrationError(
                "local orchestration fixture must be a mapping"
            )
        if fixture.get("mode") != "deterministic-local-demo":
            raise LocalOrchestrationError(
                "local orchestration requires deterministic-local-demo mode"
            )
        if not isinstance(fixture.get("attempts"), dict):
            raise LocalOrchestrationError(
                "local orchestration attempts must be a mapping"
            )
        for key in ("dispatches", "accepted_cost_events"):
            if not isinstance(fixture.get(key), list):
                raise LocalOrchestrationError(
                    f"local orchestration {key} must be a mutable list"
                )
        self._fixture = fixture
        self._completed: dict[str, EpisodeWorkflowResult] = {}

    def run_episode(
        self, workflow_input: EpisodeWorkflowInput
    ) -> EpisodeWorkflowResult:
        """Run or replay one immutable episode input."""
        workflow_id = workflow_id_for(workflow_input)
        existing = self._completed.get(workflow_id)
        if existing is not None:
            return existing

        decisions: list[SegmentDecision] = []
        for segment in workflow_input.segments:
            decision = self._run_segment(workflow_input, segment)
            decisions.append(decision)
            if decision.accepted_candidate_id is None:
                failure = WorkflowFailure(
                    segment_id=segment.segment_id,
                    attempt_count=decision.attempt,
                    failed_gates=tuple(
                        candidate_gate
                        for candidate_gate in ("fidelity", "pronunciation")
                        if not any(
                            candidate.passes_hard_gates
                            for candidate in decision.candidates
                        )
                    ),
                    last_error_code=decision.failure_code or "QUALITY_GATES_EXHAUSTED",
                )
                result = EpisodeWorkflowResult(
                    workflow_id=workflow_id,
                    status="failed",
                    decisions=tuple(decisions),
                    terminal_failure=failure,
                )
                self._completed[workflow_id] = result
                return result

        result = EpisodeWorkflowResult(
            workflow_id=workflow_id,
            status="completed",
            decisions=tuple(decisions),
            terminal_failure=None,
        )
        self._completed[workflow_id] = result
        return result

    def _run_segment(
        self,
        workflow_input: EpisodeWorkflowInput,
        segment: Any,
    ) -> SegmentDecision:
        attempts = self._attempts_for(segment.segment_id)
        last_candidates: tuple[CandidateQuality, ...] = ()
        last_error = "QUALITY_GATES_EXHAUSTED"
        for attempt in range(1, workflow_input.max_attempts + 1):
            specification = attempts[attempt - 1] if attempt <= len(attempts) else None
            if isinstance(specification, dict) and "error_code" in specification:
                last_error = str(specification["error_code"])
                continue
            if specification is None:
                last_error = "ACTIVITY_ATTEMPT_EXHAUSTED"
                continue
            if not isinstance(specification, list):
                raise LocalOrchestrationError(
                    f"attempt {attempt} for {segment.segment_id} must be a list "
                    "or error"
                )
            if len(specification) > 3:
                raise LocalOrchestrationError(
                    f"attempt {attempt} for {segment.segment_id} exceeds three takes"
                )

            candidates = tuple(
                self._candidate_from_spec(segment.segment_id, attempt, index, item)
                for index, item in enumerate(specification)
            )
            last_candidates = candidates
            selected = select_candidate(candidates)
            if selected is not None:
                self._fixture["accepted_cost_events"].append(
                    {
                        "candidate_id": selected.candidate_id,
                        "segment_id": segment.segment_id,
                    }
                )
                return SegmentDecision(
                    segment_id=segment.segment_id,
                    attempt=attempt,
                    accepted_candidate_id=selected.candidate_id,
                    candidates=candidates,
                    failure_code=None,
                )
            last_error = "QUALITY_GATES_EXHAUSTED"

        return SegmentDecision(
            segment_id=segment.segment_id,
            attempt=min(workflow_input.max_attempts, max(1, len(attempts))),
            accepted_candidate_id=None,
            candidates=last_candidates,
            failure_code=last_error,
        )

    def _attempts_for(self, segment_id: str) -> list[Any]:
        attempts = self._fixture.get("attempts", {}).get(segment_id)
        if not isinstance(attempts, list):
            raise LocalOrchestrationError(f"missing attempts for {segment_id}")
        return attempts

    def _candidate_from_spec(
        self, segment_id: str, attempt: int, take_index: int, specification: Any
    ) -> CandidateQuality:
        dispatch = {
            "segment_id": segment_id,
            "attempt": attempt,
            "take_index": take_index,
        }
        if isinstance(specification, str):
            candidate_id = f"{segment_id}-take-{take_index}"
            candidate = CandidateQuality(
                candidate_id=candidate_id,
                fidelity=FidelityResult(True, 1.0, "none"),
                diagnostics=_PASSING_DIAGNOSTICS,
                pronunciation_passed=True,
                soft_score=Decimal("1.00") - Decimal(take_index) / Decimal("100"),
            )
            self._fixture["dispatches"].append(dispatch)
            return candidate
        if not isinstance(specification, dict):
            raise LocalOrchestrationError(
                "candidate specification must be a string or object"
            )
        fidelity_data = specification.get("fidelity", {})
        diagnostics_data = specification.get("diagnostics", {})
        if not isinstance(fidelity_data, dict) or not isinstance(
            diagnostics_data, dict
        ):
            raise LocalOrchestrationError("candidate quality gates must be mappings")
        try:
            fidelity_passed = fidelity_data.get("passed", False)
            diagnostics_passed = diagnostics_data.get("passed", True)
            pronunciation_passed = specification.get("pronunciation_passed", False)
            if not isinstance(fidelity_passed, bool):
                raise LocalOrchestrationError(
                    "candidate fidelity passed must be boolean"
                )
            if not isinstance(diagnostics_passed, bool):
                raise LocalOrchestrationError(
                    "candidate diagnostics passed must be boolean"
                )
            if not isinstance(pronunciation_passed, bool):
                raise LocalOrchestrationError(
                    "candidate pronunciation passed must be boolean"
                )
            candidate_id = specification.get(
                "candidate_id", f"{segment_id}-take-{take_index}"
            )
            if not isinstance(candidate_id, str) or not candidate_id:
                raise LocalOrchestrationError("candidate_id must be a non-empty string")
            diagnostics = (
                _PASSING_DIAGNOSTICS
                if diagnostics_passed
                else replace(_PASSING_DIAGNOSTICS, clipping_ratio=1.0)
            )
            candidate = CandidateQuality(
                candidate_id=candidate_id,
                fidelity=FidelityResult(
                    fidelity_passed,
                    1.0
                    if fidelity_passed
                    else float(fidelity_data.get("accuracy", 0.0)),
                    "none" if fidelity_passed else "segment",
                ),
                diagnostics=diagnostics,
                pronunciation_passed=pronunciation_passed,
                soft_score=Decimal(str(specification.get("soft_score", "0"))),
            )
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            raise LocalOrchestrationError(
                "candidate specification is malformed"
            ) from error
        self._fixture["dispatches"].append(dispatch)
        return candidate


__all__ = [
    "LocalEpisodeWorkflowService",
    "LocalOrchestrationError",
    "TemporalEpisodeWorkflowService",
]
