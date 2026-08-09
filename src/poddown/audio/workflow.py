"""Immutable, replay-safe contracts for future Temporal audio workflows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from temporalio.common import RetryPolicy

from poddown.audio.contracts import RenderRequest
from poddown.audio.rights import VoiceConsent


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
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(_json_value(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


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
        return _json_value(self)  # type: ignore[return-value]


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
        return _json_value(self)  # type: ignore[return-value]


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
        return _json_value(self)  # type: ignore[return-value]


@dataclass(frozen=True)
class CandidateQuality:
    """Serializable quality summary; concrete metric types are future boundaries."""

    candidate_id: str
    fidelity: Any
    diagnostics: Any
    pronunciation_passed: bool
    soft_score: Decimal


@dataclass(frozen=True)
class SegmentDecision:
    """Deterministic result for one segment attempt."""

    segment_id: str
    attempt: int
    accepted_candidate_id: str | None
    candidates: tuple[CandidateQuality, ...]
    failure_code: str | None


@dataclass(frozen=True)
class EpisodeWorkflowResult:
    """Serializable terminal result returned by a future episode workflow."""

    workflow_id: str
    status: Literal["completed", "failed"]
    decisions: tuple[SegmentDecision, ...]
    terminal_failure: WorkflowFailure | None


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


__all__ = [
    "CandidateQuality",
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "EpisodeWorkflowInput",
    "EpisodeWorkflowResult",
    "MalformedAudioError",
    "NON_RETRYABLE_ERROR_TYPES",
    "RightsFailureError",
    "SegmentDecision",
    "SegmentWorkflowInput",
    "TransientActivityError",
    "WORKFLOW_ACTIVITY_RETRY_POLICY",
    "WorkflowContractError",
    "WorkflowFailure",
    "activity_key_for",
    "workflow_id_for",
]
