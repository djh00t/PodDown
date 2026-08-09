"""Immutable, replay-safe contracts for Temporal audio workflows."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from poddown.audio.contracts import RenderRequest
from poddown.audio.rights import VoiceConsent
from poddown.audio.selection import CandidateQuality, select_candidate


class WorkflowContractError(ValueError):
    """Raised when a workflow contract cannot be represented safely."""


class TransientActivityError(RuntimeError):
    """Marker error for activity failures that Temporal may retry."""


class MalformedAudioError(WorkflowContractError):
    """Marker error for invalid audio that must not be retried."""


class RightsFailureError(WorkflowContractError):
    """Marker error for failed rights checks that must not be retried."""


NON_RETRYABLE_ERROR_TYPES = (
    MalformedAudioError.__name__,
    RightsFailureError.__name__,
    WorkflowContractError.__name__,
)
ACTIVITY_CONFIGURATION_ERROR_TYPES = ("ActivityNotConfigured",)

WORKFLOW_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
)

# A descriptive alias keeps the policy discoverable without coupling callers to
# a future workflow implementation.
DEFAULT_ACTIVITY_RETRY_POLICY = WORKFLOW_ACTIVITY_RETRY_POLICY


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise WorkflowContractError(f"{name} must be a non-empty string")


def _require_positive(name: str, value: object) -> None:
    if type(value) is not int or value <= 0:
        raise WorkflowContractError(f"{name} must be a positive integer")


def _json_value(value: Any) -> Any:
    """Convert supported immutable values to canonical JSON-compatible data."""
    if is_dataclass(value):
        return {
            key: _json_value(item) for key, item in asdict(cast(Any, value)).items()
        }
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(_json_value(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _non_retryable_activity_code(error: ActivityError) -> str | None:
    cause = error.cause
    if not isinstance(cause, ApplicationError):
        return None
    if not cause.non_retryable and cause.type not in NON_RETRYABLE_ERROR_TYPES:
        return None
    return cause.type or "NON_RETRYABLE_ACTIVITY_FAILURE"


def _failed_gates_for(
    failure_code: str, candidates: tuple[CandidateQuality, ...]
) -> tuple[str, ...]:
    if failure_code == "QUALITY_GATES_EXHAUSTED":
        gates = (
            ("fidelity", any(not item.fidelity.passed for item in candidates)),
            (
                "pronunciation",
                any(not item.pronunciation_passed for item in candidates),
            ),
            (
                "audio",
                any(not item.diagnostics.passes_hard_gates for item in candidates),
            ),
        )
        return tuple(name for name, failed in gates if failed) or ("activity",)
    if failure_code == RightsFailureError.__name__:
        return ("rights",)
    if failure_code == MalformedAudioError.__name__:
        return ("audio",)
    if failure_code == WorkflowContractError.__name__:
        return ("contract",)
    return ("activity",)


def _canonical(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SegmentWorkflowInput:
    """Immutable input snapshot for one renderable episode segment."""

    segment_id: str
    render_request: RenderRequest
    consent: VoiceConsent
    critical_tokens: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty("segment_id", self.segment_id)
        if not isinstance(self.render_request, RenderRequest):
            raise WorkflowContractError("render_request must be RenderRequest")
        if self.render_request.segment_id != self.segment_id:
            raise WorkflowContractError("segment_id must match render_request")
        if not isinstance(self.consent, VoiceConsent):
            raise WorkflowContractError("consent must be VoiceConsent")
        if not isinstance(self.critical_tokens, tuple) or any(
            not isinstance(token, str) or not token for token in self.critical_tokens
        ):
            raise WorkflowContractError("critical_tokens must be a tuple of strings")

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_value(self))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SegmentWorkflowInput:
        """Reconstruct a segment snapshot from the JSON activity boundary."""
        if not isinstance(value, dict):
            raise WorkflowContractError("segment snapshot is malformed")
        request_data = value.get("render_request")
        consent_data = value.get("consent")
        critical_tokens = value.get("critical_tokens")
        if (
            not isinstance(request_data, dict)
            or not isinstance(consent_data, dict)
            or not isinstance(critical_tokens, list)
        ):
            raise WorkflowContractError("segment snapshot is malformed")
        try:
            return cls(
                segment_id=value["segment_id"],
                render_request=RenderRequest(**request_data),
                consent=VoiceConsent(
                    voice_asset_id=consent_data["voice_asset_id"],
                    evidence_id=consent_data["evidence_id"],
                    allowed_providers=frozenset(consent_data["allowed_providers"]),
                    valid=consent_data.get("valid", True),
                ),
                critical_tokens=tuple(critical_tokens),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowContractError("segment snapshot is malformed") from error


@dataclass(frozen=True)
class EpisodeWorkflowInput:
    """Immutable episode snapshot used as the workflow identity boundary."""

    episode_id: str
    episode_version: str
    segments: tuple[SegmentWorkflowInput, ...]
    max_attempts: int = 2

    def __post_init__(self) -> None:
        _require_non_empty("episode_id", self.episode_id)
        _require_non_empty("episode_version", self.episode_version)
        if not isinstance(self.segments, tuple) or any(
            not isinstance(segment, SegmentWorkflowInput) for segment in self.segments
        ):
            raise WorkflowContractError(
                "segments must be a tuple of SegmentWorkflowInput values"
            )
        _require_positive("max_attempts", self.max_attempts)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_value(self))

    def to_json(self) -> str:
        """Serialize the immutable snapshot for a Temporal payload."""
        return _canonical(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EpisodeWorkflowInput:
        """Reconstruct a snapshot from a decoded Temporal payload mapping."""
        if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
            raise WorkflowContractError("episode snapshot is malformed")
        try:
            return cls(
                episode_id=value["episode_id"],
                episode_version=value["episode_version"],
                segments=tuple(
                    SegmentWorkflowInput.from_dict(segment)
                    for segment in value["segments"]
                ),
                max_attempts=value.get("max_attempts", 2),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowContractError("episode snapshot is malformed") from error

    @classmethod
    def from_json(cls, value: str) -> EpisodeWorkflowInput:
        """Reconstruct a snapshot from a Temporal JSON payload."""
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise WorkflowContractError("episode snapshot is malformed") from error
        return cls.from_dict(decoded)


@dataclass(frozen=True)
class WorkflowFailure:
    """Structured terminal evidence suitable for Temporal workflow results."""

    segment_id: str
    attempt_count: int
    failed_gates: tuple[str, ...]
    last_error_code: str

    def __post_init__(self) -> None:
        _require_non_empty("segment_id", self.segment_id)
        _require_positive("attempt_count", self.attempt_count)
        _require_non_empty("last_error_code", self.last_error_code)
        if not isinstance(self.failed_gates, tuple) or any(
            not isinstance(gate, str) or not gate for gate in self.failed_gates
        ):
            raise WorkflowContractError("failed_gates must be a tuple of strings")

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_value(self))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WorkflowFailure:
        """Reconstruct terminal failure evidence from a JSON result."""
        if not isinstance(value, dict):
            raise WorkflowContractError("workflow failure is malformed")
        failed_gates = value.get("failed_gates")
        if not isinstance(failed_gates, list):
            raise WorkflowContractError("workflow failure is malformed")
        try:
            return cls(
                segment_id=value["segment_id"],
                attempt_count=value["attempt_count"],
                failed_gates=tuple(failed_gates),
                last_error_code=value["last_error_code"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowContractError("workflow failure is malformed") from error


@dataclass(frozen=True)
class SegmentDecision:
    """Deterministic result for one segment attempt."""

    segment_id: str
    attempt: int
    accepted_candidate_id: str | None
    candidates: tuple[CandidateQuality, ...]
    failure_code: str | None

    def __post_init__(self) -> None:
        _require_non_empty("segment_id", self.segment_id)
        _require_positive("attempt", self.attempt)
        if self.accepted_candidate_id is not None:
            _require_non_empty("accepted_candidate_id", self.accepted_candidate_id)
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(candidate, CandidateQuality) for candidate in self.candidates
        ):
            raise WorkflowContractError(
                "candidates must be a tuple of CandidateQuality values"
            )
        if self.failure_code is not None:
            _require_non_empty("failure_code", self.failure_code)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SegmentDecision:
        """Reconstruct one deterministic segment decision from JSON."""
        if not isinstance(value, dict):
            raise WorkflowContractError("segment decision is malformed")
        candidates = value.get("candidates")
        if not isinstance(candidates, list):
            raise WorkflowContractError("segment decision is malformed")
        try:
            return cls(
                segment_id=value["segment_id"],
                attempt=value["attempt"],
                accepted_candidate_id=value.get("accepted_candidate_id"),
                candidates=tuple(
                    CandidateQuality.from_dict(candidate) for candidate in candidates
                ),
                failure_code=value.get("failure_code"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowContractError("segment decision is malformed") from error


@dataclass(frozen=True)
class EpisodeWorkflowResult:
    """Serializable terminal result returned by an episode workflow."""

    workflow_id: str
    status: Literal["completed", "failed"]
    decisions: tuple[SegmentDecision, ...]
    terminal_failure: WorkflowFailure | None

    def __post_init__(self) -> None:
        _require_non_empty("workflow_id", self.workflow_id)
        if self.status not in ("completed", "failed"):
            raise WorkflowContractError("status must be completed or failed")
        if not isinstance(self.decisions, tuple) or any(
            not isinstance(decision, SegmentDecision) for decision in self.decisions
        ):
            raise WorkflowContractError(
                "decisions must be a tuple of SegmentDecision values"
            )
        if self.terminal_failure is not None and not isinstance(
            self.terminal_failure, WorkflowFailure
        ):
            raise WorkflowContractError(
                "terminal_failure must be WorkflowFailure or None"
            )
        if self.status == "completed" and self.terminal_failure is not None:
            raise WorkflowContractError("completed result cannot have terminal failure")
        if self.status == "failed" and self.terminal_failure is None:
            raise WorkflowContractError("failed result must have terminal failure")

    def to_json(self) -> str:
        """Serialize the terminal result for a Temporal workflow payload."""
        return _canonical(self)

    @classmethod
    def from_json(cls, value: str) -> EpisodeWorkflowResult:
        """Reconstruct the typed terminal result from a Temporal payload."""
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise WorkflowContractError(
                "episode workflow result is malformed"
            ) from error
        if not isinstance(decoded, dict):
            raise WorkflowContractError("episode workflow result is malformed")
        decisions = decoded.get("decisions")
        terminal_failure = decoded.get("terminal_failure")
        status = decoded.get("status")
        if (
            not isinstance(decisions, list)
            or status not in ("completed", "failed")
            or (terminal_failure is not None and not isinstance(terminal_failure, dict))
        ):
            raise WorkflowContractError("episode workflow result is malformed")
        try:
            return cls(
                workflow_id=decoded["workflow_id"],
                status=cast(Literal["completed", "failed"], status),
                decisions=tuple(
                    SegmentDecision.from_dict(decision) for decision in decisions
                ),
                terminal_failure=(
                    WorkflowFailure.from_dict(terminal_failure)
                    if terminal_failure is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowContractError(
                "episode workflow result is malformed"
            ) from error


def workflow_id_for(workflow_input: EpisodeWorkflowInput) -> str:
    """Return the stable Temporal workflow ID for an immutable episode snapshot."""
    if not isinstance(workflow_input, EpisodeWorkflowInput):
        raise TypeError("workflow_input must be EpisodeWorkflowInput")
    return f"episode-render-{_digest(workflow_input)}"


def activity_key_for(
    workflow_input: EpisodeWorkflowInput,
    stage: str,
    segment_id: str,
    *,
    attempt: int,
    take: int,
) -> str:
    """Return a stable idempotency key for one activity invocation."""
    if not isinstance(workflow_input, EpisodeWorkflowInput):
        raise TypeError("workflow_input must be EpisodeWorkflowInput")
    _require_non_empty("stage", stage)
    _require_non_empty("segment_id", segment_id)
    _require_positive("attempt", attempt)
    if type(take) is not int or take < 0:
        raise WorkflowContractError("take must be a non-negative integer")
    payload = {
        "episode": workflow_input.to_dict(),
        "stage": stage,
        "segment_id": segment_id,
        "attempt": attempt,
        "take": take,
    }
    return f"activity-{_digest(payload)}"


def _non_retryable_activity_code(error: ActivityError) -> str | None:
    """Return a terminal application-error type carried by an activity error."""
    cause = error.cause
    if not isinstance(cause, ApplicationError):
        return None
    if not cause.non_retryable and cause.type not in NON_RETRYABLE_ERROR_TYPES:
        return None
    return cause.type or "NON_RETRYABLE_ACTIVITY_FAILURE"


def _failed_gates_for(failure_code: str) -> tuple[str, ...]:
    """Map terminal activity classes to the gates that actually failed."""
    if failure_code == RightsFailureError.__name__:
        return ("rights",)
    if failure_code == MalformedAudioError.__name__:
        return ("audio",)
    if failure_code == WorkflowContractError.__name__:
        return ("contract",)
    if failure_code in ACTIVITY_CONFIGURATION_ERROR_TYPES:
        return ("configuration",)
    return ("activity",)


RENDER_SEGMENT_ACTIVITY_NAME = "poddown.audio.render_segment"


@activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
async def render_segment_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Fail closed until a worker supplies the provider-bound activity handler."""
    del payload
    raise ApplicationError(
        "render segment activity handler is not configured",
        type="ActivityNotConfigured",
        non_retryable=True,
    )


@workflow.defn(name="EpisodeRenderWorkflow")
class EpisodeRenderWorkflow:
    """Temporal workflow for bounded segment fan-out and deterministic repair."""

    @workflow.run
    async def run(self, episode_input_json: str) -> str:
        """Render, select, and repair segments from an immutable JSON snapshot."""
        episode_input = EpisodeWorkflowInput.from_json(episode_input_json)
        decisions: list[SegmentDecision] = []
        terminal_failure: WorkflowFailure | None = None
        for segment in episode_input.segments:
            decision = await self._run_segment(episode_input, segment)
            decisions.append(decision)
            if decision.accepted_candidate_id is None:
                failure_code = decision.failure_code or "QUALITY_GATES_EXHAUSTED"
                terminal_failure = WorkflowFailure(
                    segment_id=segment.segment_id,
                    attempt_count=decision.attempt,
                    failed_gates=_failed_gates_for(
                        decision.failure_code or "QUALITY_GATES_EXHAUSTED",
                        decision.candidates,
                    ),
                    last_error_code=decision.failure_code or "QUALITY_GATES_EXHAUSTED",
                )
                break
        result = EpisodeWorkflowResult(
            workflow_id=workflow_id_for(episode_input),
            status="failed" if terminal_failure else "completed",
            decisions=tuple(decisions),
            terminal_failure=terminal_failure,
        )
        return result.to_json()

    async def _run_segment(
        self, episode_input: EpisodeWorkflowInput, segment: SegmentWorkflowInput
    ) -> SegmentDecision:
        last_candidates: tuple[CandidateQuality, ...] = ()
        last_error = "QUALITY_GATES_EXHAUSTED"
        for attempt in range(1, episode_input.max_attempts + 1):
            activity_calls = [
                workflow.execute_activity(
                    RENDER_SEGMENT_ACTIVITY_NAME,
                    args=[
                        {
                            "episode": episode_input.to_dict(),
                            "segment": segment.to_dict(),
                            "attempt": attempt,
                            "take": take,
                            "activity_key": activity_key_for(
                                episode_input,
                                "render",
                                segment.segment_id,
                                attempt=attempt,
                                take=take,
                            ),
                        }
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=WORKFLOW_ACTIVITY_RETRY_POLICY,
                    activity_id=activity_key_for(
                        episode_input,
                        "render",
                        segment.segment_id,
                        attempt=attempt,
                        take=take,
                    ),
                )
                for take in range(3)
            ]
            results = await asyncio.gather(*activity_calls, return_exceptions=True)
            activity_errors = tuple(
                result for result in results if isinstance(result, ActivityError)
            )
            try:
                last_candidates = tuple(
                    CandidateQuality.from_dict(result)
                    for result in results
                    if isinstance(result, dict)
                )
            except ValueError:
                return SegmentDecision(
                    segment_id=segment.segment_id,
                    attempt=attempt,
                    accepted_candidate_id=None,
                    candidates=(),
                    failure_code="MALFORMED_ACTIVITY_OUTPUT",
                )
            terminal_error = next(
                (
                    code
                    for code in (
                        _non_retryable_activity_code(error) for error in activity_errors
                    )
                    if code is not None
                ),
                None,
            )
            if terminal_error is not None:
                return SegmentDecision(
                    segment_id=segment.segment_id,
                    attempt=attempt,
                    accepted_candidate_id=None,
                    candidates=last_candidates,
                    failure_code=terminal_error,
                )
            if activity_errors and not last_candidates:
                last_error = "ACTIVITY_RETRY_EXHAUSTED"
                continue
            selected = select_candidate(last_candidates)
            if selected is not None:
                return SegmentDecision(
                    segment_id=segment.segment_id,
                    attempt=attempt,
                    accepted_candidate_id=selected.candidate_id,
                    candidates=last_candidates,
                    failure_code=None,
                )
            last_error = "QUALITY_GATES_EXHAUSTED"
        return SegmentDecision(
            segment_id=segment.segment_id,
            attempt=episode_input.max_attempts,
            accepted_candidate_id=None,
            candidates=last_candidates,
            failure_code=last_error,
        )


__all__ = [
    "CandidateQuality",
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "EpisodeWorkflowInput",
    "EpisodeWorkflowResult",
    "EpisodeRenderWorkflow",
    "MalformedAudioError",
    "NON_RETRYABLE_ERROR_TYPES",
    "RightsFailureError",
    "RENDER_SEGMENT_ACTIVITY_NAME",
    "SegmentDecision",
    "SegmentWorkflowInput",
    "TransientActivityError",
    "WORKFLOW_ACTIVITY_RETRY_POLICY",
    "WorkflowContractError",
    "WorkflowFailure",
    "activity_key_for",
    "render_segment_activity",
    "workflow_id_for",
]
